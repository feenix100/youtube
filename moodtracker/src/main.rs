use chrono::{Local, NaiveDate};
use eframe::{egui, egui::{Color32, Pos2, Rect, Rounding, Stroke, Vec2}};
use serde::{Deserialize, Serialize};
use std::{fs::File, io::BufWriter, time::{Duration, Instant}};

// PDF
use printpdf::*;

#[derive(Serialize, Deserialize, Default, Clone)]
struct Settings {
    // Per-day summary: (YYYY-MM-DD, mood 0..4)
    history: Vec<(String, i32)>,
    // Full session log of choices: (timestamp, mood 0..4)
    entries: Vec<(String, i32)>,
}

struct App {
    settings: Settings,
    dirty: bool,
    show_prompt: bool,
    next_prompt_at: Instant,
    // transient UI status
    last_status: Option<String>,
}

impl App {
    fn new(_cc: &eframe::CreationContext<'_>) -> Self {
        let settings: Settings = confy::load("mood_tracker", "default").unwrap_or_default();
        Self {
            settings,
            dirty: false,
            show_prompt: false,
            next_prompt_at: Instant::now() + Duration::from_secs(5),
            last_status: None,
        }
    }

    fn today_key() -> String {
        let d: NaiveDate = Local::now().date_naive();
        d.format("%Y-%m-%d").to_string()
    }

    fn mood_name(m: i32) -> &'static str {
        match m {
            4 => "Happy",
            3 => "Good",
            2 => "Okay",
            1 => "Sad",
            _ => "Angry",
        }
    }

    fn set_today(&mut self, mood: i32) {
        let ts = Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
        self.settings.entries.push((ts, mood));

        // daily summary (overwrite today's value)
        let key = Self::today_key();
        if let Some((_, v)) = self.settings.history.iter_mut().find(|(k, _)| *k == key) {
            *v = mood;
        } else {
            self.settings.history.push((key, mood));
        }

        self.dirty = true;
        self.show_prompt = false;
    }

    fn draw_chart(ui: &mut egui::Ui, data: &[(String, i32)]) {
        ui.label("Last 14 days");
        let count = data.len().min(14);
        let slice = &data[data.len().saturating_sub(count)..];

        let desired = Vec2::new(ui.available_width(), 160.0);
        let (rect, _response) = ui.allocate_exact_size(desired, egui::Sense::hover());
        let painter = ui.painter();

        painter.rect(
            rect,
            Rounding::same(8.0),
            Color32::from_gray(20),
            Stroke::new(1.0, Color32::from_gray(80)),
        );

        if slice.is_empty() {
            painter.text(
                rect.center(),
                egui::Align2::CENTER_CENTER,
                "No data yet — log a mood!",
                egui::TextStyle::Body.resolve(ui.style()),
                Color32::WHITE,
            );
            return;
        }

        let margin = 12.0;
        let plot = Rect::from_min_max(
            rect.min + Vec2::splat(margin),
            rect.max - Vec2::splat(margin),
        );

        let max_val = 4.0;
        let bar_w = plot.width() / (slice.len() as f32).max(1.0);
        for (i, (_date, mood)) in slice.iter().enumerate() {
            let x0 = plot.left() + i as f32 * bar_w + 4.0;
            let x1 = x0 + bar_w - 8.0;

            let h = ((*mood as f32) / max_val).clamp(0.0, 1.0) * plot.height();
            let y1 = plot.bottom();
            let y0 = y1 - h;

            let col = match *mood {
                4 => Color32::from_rgb(90, 220, 120),   // Happy
                3 => Color32::from_rgb(160, 220, 120),  // Good
                2 => Color32::from_rgb(220, 220, 120),  // Okay
                1 => Color32::from_rgb(230, 170, 120),  // Sad
                _ => Color32::from_rgb(230, 120, 120),  // Angry
            };

            painter.rect_filled(
                Rect::from_min_max(Pos2::new(x0, y0), Pos2::new(x1, y1)),
                Rounding::same(4.0),
                col,
            );
        }
    }

    fn mood_buttons(ui: &mut egui::Ui, on_pick: &mut dyn FnMut(i32)) {
        ui.horizontal_wrapped(|ui| {
            ui.label("Select mood:");
            if ui.button("Happy").clicked() { on_pick(4); }
            if ui.button("Good").clicked()  { on_pick(3); }
            if ui.button("Okay").clicked()  { on_pick(2); }
            if ui.button("Sad").clicked()   { on_pick(1); }
            if ui.button("Angry").clicked() { on_pick(0); }
        });
    }

    fn build_session_text(&self) -> String {
        let mut s = String::new();
        s.push_str("Mood Tracker — Session Log\n");
        s.push_str("----------------------------------------\n");
        for (ts, m) in &self.settings.entries {
            s.push_str(&format!("{ts} — {}\n", Self::mood_name(*m)));
        }
        s
    }

    fn save_log_txt(&mut self) {
        if self.settings.entries.is_empty() {
            self.last_status = Some("No entries to save yet.".to_string());
            return;
        }
        let default_name = format!("session_log_{}.txt", Local::now().format("%Y%m%d_%H%M%S"));
        if let Some(path) = rfd::FileDialog::new()
            .set_file_name(&default_name)
            .add_filter("Text", &["txt"])
            .save_file()
        {
            let content = self.build_session_text();
            match std::fs::write(&path, content) {
                Ok(_) => self.last_status = Some(format!("Saved: {}", path.display())),
                Err(e) => self.last_status = Some(format!("Failed to save TXT: {e}")),
            }
        }
    }

    fn save_log_pdf(&mut self) {
        if self.settings.entries.is_empty() {
            self.last_status = Some("No entries to save yet.".to_string());
            return;
        }

        // 1) Ask for a TTF font (portable & reliable)
        let font_path = match rfd::FileDialog::new()
            .add_filter("TrueType Font", &["ttf"])
            .set_title("Select a .ttf font (e.g., Arial.ttf)")
            .pick_file()
        {
            Some(p) => p,
            None => {
                self.last_status = Some("PDF export cancelled (no font selected).".to_string());
                return;
            }
        };

        // 2) Ask where to save the PDF
        let default_name = format!("session_log_{}.pdf", Local::now().format("%Y%m%d_%H%M%S"));
        let Some(save_path) = rfd::FileDialog::new()
            .set_file_name(&default_name)
            .add_filter("PDF", &["pdf"])
            .save_file()
        else {
            self.last_status = Some("PDF export cancelled.".to_string());
            return;
        };

        // 3) Build the PDF
        let doc_title = "Mood Tracker — Session Log";
        let (doc, page, layer) = PdfDocument::new(doc_title, Mm(210.0), Mm(297.0), "Layer 1");
        let current_layer = doc.get_page(page).get_layer(layer);

        // Load chosen font
        let mut font_file = match File::open(&font_path) {
            Ok(f) => f,
            Err(e) => {
                self.last_status = Some(format!("Failed to open font: {e}"));
                return;
            }
        };
        let font = match doc.add_external_font(&mut font_file) {
            Ok(f) => f,
            Err(e) => {
                self.last_status = Some(format!("Failed to load font: {e}"));
                return;
            }
        };

        // Page layout
        let margin_l = Mm(15.0);
        let margin_r = Mm(15.0);
        let margin_t = Mm(15.0);
        let margin_b = Mm(20.0);
        let page_w = Mm(210.0);
        let page_h = Mm(297.0);
        let usable_w = page_w.0 - margin_l.0 - margin_r.0;

        let mut cursor_y = page_h.0 - margin_t.0;

        // Title
        let title_size = 16.0;
        current_layer.use_text(doc_title, title_size, margin_l, printpdf::Mm(cursor_y), &font);
        cursor_y -= 10.0;

        // Divider
        let y_mm = printpdf::Mm(cursor_y);
        current_layer.add_shape(
            printpdf::Line {
                points: vec![
                    (printpdf::Point::new(margin_l, y_mm), false),
                    (printpdf::Point::new(page_w - margin_r, y_mm), false),
                ],
                is_closed: false,
                has_fill: false,
                has_stroke: true,
                is_clipping_path: false,
            }
                .into(),
        );
        cursor_y -= 6.0;

        // Text body: simple wrapping by characters
        let body_size = 11.0;
        let line_height = 6.0; // mm
        let max_chars_per_line = ((usable_w / 2.5) as usize).max(25); // rough wrap width tuned for ~11pt

        let mut write_line = |layer: &PdfLayerReference, text: &str, mut cursor_y_mm: f64| -> f64 {
            layer.use_text(text, body_size, margin_l, printpdf::Mm(cursor_y_mm), &font);

            cursor_y_mm - line_height
        };

        // Iterate entries (newest last, top-down)
        let content_lines = self.settings.entries.iter().map(|(ts, m)| {
            format!("{ts} — {}", Self::mood_name(*m))
        });

        // helper to create a new page when we run out of space
        let mut ensure_space = |cursor_y_mm: f64, doc: &PdfDocumentReference| -> (PdfLayerReference, f64) {
            if cursor_y_mm <= margin_b.0 + line_height {
                let (np, nl) = doc.add_page(page_w, page_h, "Layer");
                (doc.get_page(np).get_layer(nl), page_h.0 - margin_t.0)
            } else {
                (current_layer.clone(), cursor_y_mm)
            }
        };

        let mut cur_layer = current_layer.clone();
        for line in content_lines {
            // naive wrap
            if line.len() <= max_chars_per_line {
                (cur_layer, cursor_y) = ensure_space(cursor_y, &doc);
                cursor_y = write_line(&cur_layer, &line, cursor_y);
            } else {
                let mut start = 0;
                while start < line.len() {
                    let end = (start + max_chars_per_line).min(line.len());
                    let chunk = &line[start..end];
                    (cur_layer, cursor_y) = ensure_space(cursor_y, &doc);
                    cursor_y = write_line(&cur_layer, chunk, cursor_y);
                    start = end;
                }
            }
        }

        // Save
        if let Err(e) = doc.save(&mut BufWriter::new(File::create(&save_path).unwrap())) {
            self.last_status = Some(format!("Failed to save PDF: {e}"));
        } else {
            self.last_status = Some(format!("Saved: {}", save_path.display()));
        }
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Periodic prompt
        if Instant::now() >= self.next_prompt_at {
            self.show_prompt = true;
            self.next_prompt_at = Instant::now() + Duration::from_secs(5);
        }
        ctx.request_repaint_after(Duration::from_millis(100));

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.heading("Mood Tracker (Rust + egui)");
            if let Some(s) = &self.last_status {
                ui.colored_label(egui::Color32::LIGHT_GREEN, s);
            }
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            // Mood buttons
            let mut picked = None;
            App::mood_buttons(ui, &mut |m| picked = Some(m));
            if let Some(m) = picked { self.set_today(m); }

            ui.separator();

            // Chart (daily)
            self.settings.history.sort_by(|a, b| a.0.cmp(&b.0));
            App::draw_chart(ui, &self.settings.history);

            ui.add_space(8.0);

            // Export buttons
            ui.horizontal(|ui| {
                if ui.button("Save Log (.txt)").clicked() {
                    self.save_log_txt();
                }
                if ui.button("Save Log as PDF").clicked() {
                    self.save_log_pdf();
                }
            });

            ui.add_space(4.0);

            // Visible session list
            egui::ScrollArea::vertical().max_height(220.0).show(ui, |ui| {
                ui.heading("Session Log");
                for (ts, m) in self.settings.entries.iter().rev() {
                    ui.label(format!("{ts}  —  {}", App::mood_name(*m)));
                }
            });
        });

        // Pop-up prompt every 5s
        if self.show_prompt {
            egui::Window::new("Quick mood check")
                .collapsible(false)
                .resizable(false)
                .default_size(Vec2::new(320.0, 90.0))
                .anchor(egui::Align2::CENTER_CENTER, Vec2::ZERO)
                .show(ctx, |ui| {
                    ui.label("How do you feel right now?");
                    let mut picked = None;
                    App::mood_buttons(ui, &mut |m| picked = Some(m));
                    ui.horizontal(|ui| {
                        if ui.button("Skip").clicked() {
                            self.show_prompt = false;
                        }
                    });
                    if let Some(m) = picked { self.set_today(m); }
                });
        }

        if self.dirty {
            let _ = confy::store("mood_tracker", "default", &self.settings);
            self.dirty = false;
        }
    }
}

fn main() -> eframe::Result<()> {
    let native_opts = eframe::NativeOptions::default();
    eframe::run_native(
        "Mood Tracker",
        native_opts,
        Box::new(|cc| Box::new(App::new(cc))),
    )
}
