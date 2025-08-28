use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::Instant;

use anyhow::{anyhow, Context, Result};
use chrono::{Datelike, Local, NaiveDate};
use directories::ProjectDirs;
use eframe::{egui, egui::Color32};
use egui_extras::DatePickerButton;
use serde::{Deserialize, Serialize};

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions::default();
    eframe::run_native(
        "Local Weather (Open-Meteo)",
        native_options,
        Box::new(|cc| Ok(Box::new(WeatherApp::new(cc)))),
    )
}

#[derive(Clone, Copy, PartialEq)]
enum AnimMode {
    Auto,
    Sunny,
    Rain,
    Snow,
    Cloud,
}

struct WeatherApp {
    // Inputs
    city: String,
    state: String,
    date: NaiveDate,

    // UI state
    status: String,
    last_result: Option<FetchedWeather>,
    loading: bool,
    start_time: Instant,

    // async channel
    rx: mpsc::Receiver<AppMsg>,
    tx: mpsc::Sender<AppMsg>,

    // log
    log_entries: Vec<FetchedWeather>,
    log_path: PathBuf,

    // animation override
    anim_override: AnimMode,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FetchedWeather {
    timestamp: String, // ISO local time when fetched
    city: String,
    state: String,
    date: String,
    latitude: f64,
    longitude: f64,
    timezone: String,
    source: String, // forecast | archive
    temp_max_c: f64,
    temp_min_c: f64,
    precipitation_mm: f64,
}

enum AppMsg {
    Fetched(Result<FetchedWeather>),
}

impl WeatherApp {
    fn new(_cc: &eframe::CreationContext<'_>) -> Self {
        let (tx, rx) = mpsc::channel();
        let (log_entries, log_path) = load_log();
        Self {
            city: "Phoenix".to_string(),
            state: "AZ".to_string(),
            date: Local::now().date_naive(),

            status: "Ready".to_string(),
            last_result: None,
            loading: false,
            start_time: Instant::now(),

            rx,
            tx,

            log_entries,
            log_path,

            anim_override: AnimMode::Auto,
        }
    }
}

impl eframe::App for WeatherApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Handle async results
        while let Ok(msg) = self.rx.try_recv() {
            match msg {
                AppMsg::Fetched(res) => {
                    self.loading = false;
                    match res {
                        Ok(data) => {
                            self.status = format!(
                                "{} on {} → max: {:.1}°C | min: {:.1}°C | precip: {:.1} mm ({})",
                                data.city, data.date, data.temp_max_c, data.temp_min_c, data.precipitation_mm, data.source
                            );
                            self.last_result = Some(data.clone());
                            // persist to log
                            if let Err(e) = append_log(&self.log_path, &data) {
                                self.status = format!("Saved result, but failed to log: {e}");
                            }
                            self.log_entries.push(data);
                        }
                        Err(e) => {
                            self.status = format!("Error: {e}");
                        }
                    }
                }
            }
        }

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.heading("Local Weather (Open-Meteo)");
            ui.label("Enter City, State, and Date. Click Fetch.");
        });

        egui::SidePanel::left("controls").resizable(true).show(ctx, |ui| {
            ui.spacing_mut().item_spacing = egui::vec2(8.0, 8.0);
            ui.separator();

            ui.horizontal(|ui| {
                ui.label("City:");
                ui.text_edit_singleline(&mut self.city);
            });

            ui.horizontal(|ui| {
                ui.label("State:");
                ui.text_edit_singleline(&mut self.state);
            });

            ui.horizontal(|ui| {
                ui.label("Date:");
                let mut d = self.date;
                if ui.add(DatePickerButton::new(&mut d)).changed() {
                    self.date = d;
                }
            });

            // Animation override dropdown
            ui.horizontal(|ui| {
                ui.label("Animation:");
                egui::ComboBox::from_id_source("anim_mode_combo")
                    .selected_text(match self.anim_override {
                        AnimMode::Auto => "Auto",
                        AnimMode::Sunny => "Sunny",
                        AnimMode::Rain => "Rain",
                        AnimMode::Snow => "Snow",
                        AnimMode::Cloud => "Cloud",
                    })
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut self.anim_override, AnimMode::Auto, "Auto");
                        ui.selectable_value(&mut self.anim_override, AnimMode::Sunny, "Sunny");
                        ui.selectable_value(&mut self.anim_override, AnimMode::Rain, "Rain");
                        ui.selectable_value(&mut self.anim_override, AnimMode::Snow, "Snow");
                        ui.selectable_value(&mut self.anim_override, AnimMode::Cloud, "Cloud");
                    });
            });

            ui.horizontal(|ui| {
                if ui.add_enabled(!self.loading, egui::Button::new("Fetch Weather")).clicked() {
                    let city = self.city.trim().to_string();
                    let state = self.state.trim().to_string();
                    let date = self.date;
                    self.loading = true;
                    self.status = "Fetching…".to_string();
                    let tx = self.tx.clone();
                    thread::spawn(move || {
                        let res = fetch_weather_flow(&city, &state, date);
                        let _ = tx.send(AppMsg::Fetched(res));
                    });
                }
                if self.loading {
                    ui.add(egui::Spinner::new());
                }
            });

            ui.separator();
            ui.label(format!("Status: {}", self.status));

            if let Some(last) = &self.last_result {
                ui.separator();
                ui.heading("Last Result");
                egui::Grid::new("last_grid").num_columns(2).show(ui, |ui| {
                    ui.label("Location:"); ui.label(format!("{}, {}", last.city, last.state)); ui.end_row();
                    ui.label("Date:"); ui.label(&last.date); ui.end_row();
                    ui.label("Coords:"); ui.label(format!("{:.4}, {:.4}", last.latitude, last.longitude)); ui.end_row();
                    ui.label("Timezone:"); ui.label(&last.timezone); ui.end_row();
                    ui.label("Source:"); ui.label(&last.source); ui.end_row();
                    ui.label("Temp max/min:"); ui.label(format!("{:.1} / {:.1} °C", last.temp_max_c, last.temp_min_c)); ui.end_row();
                    ui.label("Precip:"); ui.label(format!("{:.1} mm", last.precipitation_mm)); ui.end_row();
                });
            }
        });

        egui::SidePanel::right("log").resizable(true).show(ctx, |ui| {
            ui.heading("Log");
            ui.horizontal(|ui| {
                if ui.button("Export CSV").clicked() {
                    if let Err(e) = export_csv(&self.log_path, &self.log_entries) {
                        self.status = format!("Export failed: {e}");
                    } else {
                        self.status = format!("Exported CSV alongside log at {}", self.log_path.display());
                    }
                }
                if ui.button("Clear log").clicked() {
                    if let Err(e) = clear_log(&self.log_path) {
                        self.status = format!("Clear failed: {e}");
                    } else {
                        self.log_entries.clear();
                        self.status = "Log cleared".to_string();
                    }
                }
            });
            ui.separator();
            egui::ScrollArea::vertical().auto_shrink([false; 2]).show(ui, |ui| {
                for (i, e) in self.log_entries.iter().enumerate().rev() {
                    ui.collapsing(format!("{} · {}, {}", e.timestamp, e.city, e.state), |ui| {
                        ui.monospace(serde_json::to_string_pretty(e).unwrap_or_default());
                    });
                    if i > 0 { ui.separator(); }
                }
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            // Animated scene
            let t = self.start_time.elapsed().as_secs_f32();
            let available = ui.available_size_before_wrap();
            let size = available.min_elem().min(420.0);
            let (rect, _resp) = ui.allocate_exact_size(egui::vec2(size, size), egui::Sense::hover());
            let painter = ui.painter_at(rect);
            let center = rect.center();

            // Subtle background rings
            for i in 0..5 {
                let r = (size / 2.2) - i as f32 * 14.0 + (t * 5.0 + i as f32 * 0.7).sin() * 1.0;
                let alpha = (20 - i as i32 * 3).max(5) as u8;
                painter.circle_stroke(
                    center,
                    r,
                    egui::Stroke::new(1.0, Color32::from_rgba_unmultiplied(200, 200, 220, alpha)),
                );
            }

            // Determine animation mode from override or last result
            let mut mode = "sunny";
            match self.anim_override {
                AnimMode::Sunny => mode = "sunny",
                AnimMode::Rain => mode = "rain",
                AnimMode::Snow => mode = "snow",
                AnimMode::Cloud => mode = "cloud",
                AnimMode::Auto => {
                    if let Some(last) = &self.last_result {
                        if last.precipitation_mm >= 0.5 {
                            mode = "rain";
                        } else if last.temp_max_c < 5.0 {
                            mode = "snow";
                        } else if last.temp_max_c < 18.0 {
                            mode = "cloud";
                        } else {
                            mode = "sunny";
                        }
                    } else {
                        mode = "sunny";
                    }
                }
            }

            // Base sun (used by sunny/cloud too)
            let base_r = size * 0.12;
            let pulse = 1.0 + 0.07 * (t * 2.2).sin();
            let sun_r = base_r * pulse;

            match mode {
                "sunny" => {
                    painter.circle_filled(center, sun_r, Color32::from_rgb(255, 210, 80));
                    // Rays spinning
                    let rays = 10;
                    let spin = t * 0.7;
                    for k in 0..rays {
                        let ang = spin + (k as f32) * (std::f32::consts::TAU / rays as f32);
                        let dir = egui::vec2(ang.cos(), ang.sin());
                        let a = center + dir * (sun_r + 6.0);
                        let b = center + dir * (sun_r + 26.0 + 4.0 * (t * 3.1 + k as f32).sin().abs());
                        painter.line_segment(
                            [a, b],
                            egui::Stroke::new(2.0, Color32::from_rgb(255, 180, 40)),
                        );
                    }
                }
                "cloud" => {
                    // Moving clouds pass in front of the sun
                    painter.circle_filled(center, sun_r, Color32::from_rgb(255, 210, 80));
                    // Clouds: several overlapping circles; drift horizontally
                    let drift = (t * 30.0).sin() * (size * 0.15);
                    let cloud_center = egui::pos2(center.x + drift, center.y + size * 0.05);
                    let radii = [size * 0.10, size * 0.08, size * 0.07, size * 0.06];
                    let offsets = [
                        egui::vec2(-radii[0] * 0.6, 0.0),
                        egui::vec2(0.0, -radii[1] * 0.2),
                        egui::vec2(radii[2] * 0.6, 0.0),
                        egui::vec2(radii[3] * 1.1, 0.05 * size),
                    ];
                    for (r, off) in radii.iter().zip(offsets.iter()) {
                        painter.circle_filled(
                            cloud_center + *off,
                            *r,
                            Color32::from_rgb(235, 240, 245),
                        );
                        painter.circle_stroke(
                            cloud_center + *off,
                            *r,
                            egui::Stroke::new(1.0, Color32::from_rgb(210, 215, 225)),
                        );
                    }
                }
                "rain" => {
                    // Dim sun / gray sky
                    painter.rect_filled(
                        rect.shrink(2.0),
                        6.0,
                        Color32::from_rgb(230, 235, 245),
                    );
                    let sun_c = egui::pos2(center.x, center.y - size * 0.18);
                    painter.circle_filled(sun_c, sun_r * 0.7, Color32::from_rgb(255, 220, 120));

                    // Cloud over sun
                    let cloud_center = egui::pos2(center.x, center.y - size * 0.10);
                    let radii = [size * 0.12, size * 0.10, size * 0.09];
                    let offsets = [
                        egui::vec2(-radii[0] * 0.6, 0.0),
                        egui::vec2(0.0, -radii[1] * 0.2),
                        egui::vec2(radii[2] * 0.7, 0.05 * size),
                    ];
                    for (r, off) in radii.iter().zip(offsets.iter()) {
                        painter.circle_filled(
                            cloud_center + *off,
                            *r,
                            Color32::from_rgb(220, 225, 235),
                        );
                    }

                    // Raindrops: falling slanted lines
                    let drops = 80;
                    let slope = egui::vec2(0.3, 1.0).normalized();
                    let fall_speed = size * 0.6; // px/sec
                    for i in 0..drops {
                        // pseudo-random positions based on i (no RNG needed)
                        let seed_x = ((i as f32 * 127.1).sin() * 43758.5453).fract();
                        let seed_y = ((i as f32 * 311.7).sin() * 12543.1234).fract();

                        let x = rect.left() + seed_x.abs() * rect.width();
                        // loop vertical with time
                        let y0 = rect.top() + (seed_y.abs() * rect.height()
                            + (t * fall_speed) % rect.height());
                        let y = if y0 > rect.bottom() { y0 - rect.height() } else { y0 };

                        let p0 = egui::pos2(x, y);
                        let p1 = p0 + slope * 14.0;
                        painter.line_segment(
                            [p0, p1],
                            egui::Stroke::new(1.6, Color32::from_rgb(120, 170, 255)),
                        );
                    }
                }
                "snow" => {
                    // Cool background
                    painter.rect_filled(
                        rect.shrink(2.0),
                        6.0,
                        Color32::from_rgb(240, 245, 255),
                    );

                    // Snowflakes: drifting circles
                    let flakes = 70;
                    let fall_speed = size * 0.25;
                    for i in 0..flakes {
                        let sx = ((i as f32 * 83.1).sin() * 15437.77).fract();
                        let sy = ((i as f32 * 17.7).sin() * 937.13).fract();
                        let radius = 1.5 + ((i * 7) % 3) as f32; // 1.5..3.5

                        let x = rect.left()
                            + sx.abs() * rect.width()
                            + (t * 12.0 + i as f32 * 0.7).sin() * 8.0;
                        let y0 = rect.top() + sy.abs() * rect.height()
                            + (t * fall_speed) % rect.height();
                        let y = if y0 > rect.bottom() { y0 - rect.height() } else { y0 };

                        let p = egui::pos2(x, y);
                        painter.circle_filled(p, radius, Color32::from_rgb(250, 252, 255));
                        painter.circle_stroke(
                            p,
                            radius,
                            egui::Stroke::new(1.0, Color32::from_rgb(220, 230, 245)),
                        );
                    }
                }
                _ => {}
            }

            // Loading overlay: three rotating arc segments (polyline approximation)
            if self.loading {
                let arcs = 3;
                for j in 0..arcs {
                    let r = sun_r + 40.0 + j as f32 * 16.0;
                    let frac = ((t * 1.8 + j as f32) % 1.0).fract();
                    let start = frac * std::f32::consts::TAU;
                    let end = start + std::f32::consts::TAU * 0.33;
                    let steps = 24;
                    let mut points = Vec::with_capacity(steps + 1);
                    for k in 0..=steps {
                        let ang = start + (end - start) * (k as f32 / steps as f32);
                        points.push(center + egui::vec2(ang.cos(), ang.sin()) * r);
                    }
                    painter.add(egui::Shape::line(
                        points,
                        egui::Stroke::new(2.0, Color32::from_rgb(120, 170, 255)),
                    ));
                }
                ctx.request_repaint(); // continuous
            } else {
                ctx.request_repaint_after(std::time::Duration::from_millis(33)); // ~30 FPS idle
            }
        });
    }
}

// =============== Fetch Flow ==================

fn fetch_weather_flow(city: &str, state: &str, date: NaiveDate) -> Result<FetchedWeather> {
    // Geocode
    let (lat, lon, tz, norm_city, norm_state) = geocode_city_state(city, state)?;

    // Prefer forecast API; if it returns empty, try archive
    let date_s = date.to_string();

    if let Ok(mut d) = fetch_daily_forecast(lat, lon, &date_s, tz.as_deref()) {
        d.city = norm_city.clone();
        d.state = norm_state.clone();
        return Ok(d);
    }

    let mut d = fetch_daily_archive(lat, lon, &date_s, tz.as_deref())?;
    d.city = norm_city;
    d.state = norm_state;
    Ok(d)
}

// =============== Open-Meteo: Geocoding ==================

#[derive(Deserialize)]
struct GeoResponse {
    results: Option<Vec<GeoPlace>>,
}

#[derive(Deserialize)]
struct GeoPlace {
    name: String,
    latitude: f64,
    longitude: f64,
    #[serde(default)]
    admin1: Option<String>,
    #[serde(default)]
    timezone: Option<String>,
    country_code: String,
}

fn geocode_city_state(city: &str, state: &str) -> Result<(f64, f64, Option<String>, String, String)> {
    let state_norm = resolve_state_name(state.trim());
    let url = format!(
        "https://geocoding-api.open-meteo.com/v1/search?name={}&count=10&language=en&format=json&country=US",
        urlencoding::encode(city.trim())
    );
    let resp: GeoResponse = ureq::get(&url)
        .call()
        .context("geocoding request failed")?
        .into_json()
        .context("invalid geocoding JSON")?;

    let results = resp
        .results
        .ok_or_else(|| anyhow!("No geocoding results for '{city}, {state}'"))?;

    let mut best: Option<&GeoPlace> = None;
    for r in &results {
        if r.country_code.to_uppercase() == "US" {
            if let Some(adm) = &r.admin1 {
                if adm.eq_ignore_ascii_case(&state_norm)
                    || adm.to_lowercase().contains(&state_norm.to_lowercase())
                {
                    best = Some(r);
                    break;
                }
            }
        }
    }
    let chosen = best
        .or_else(|| results.iter().find(|r| r.country_code.to_uppercase() == "US"))
        .ok_or_else(|| anyhow!("No US match for '{city}, {state}'"))?;

    Ok((
        chosen.latitude,
        chosen.longitude,
        chosen.timezone.clone(),
        chosen.name.clone(),
        state_norm,
    ))
}

// =============== Open-Meteo: Forecast / Archive ==================

#[derive(Deserialize)]
struct WeatherResp {
    daily: Option<DailyBlock>,
    #[serde(default)]
    timezone: Option<String>,
}

#[derive(Deserialize)]
struct DailyBlock {
    time: Vec<String>,
    temperature_2m_max: Vec<f64>,
    temperature_2m_min: Vec<f64>,
    #[serde(default)]
    precipitation_sum: Vec<f64>,
}

fn fetch_daily_forecast(lat: f64, lon: f64, date: &str, tz: Option<&str>) -> Result<FetchedWeather> {
    let tzp = tz.unwrap_or("auto");
    let url = format!(
        "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&start_date={date}&end_date={date}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone={}",
        urlencoding::encode(tzp)
    );
    let resp: WeatherResp = ureq::get(&url)
        .call()
        .context("forecast request failed")?
        .into_json()
        .context("invalid forecast JSON")?;
    extract_one_day(resp, lat, lon, date, "forecast")
}

fn fetch_daily_archive(lat: f64, lon: f64, date: &str, tz: Option<&str>) -> Result<FetchedWeather> {
    let tzp = tz.unwrap_or("auto");
    let url = format!(
        "https://archive-api.open-meteo.com/v1/era5?latitude={lat}&longitude={lon}&start_date={date}&end_date={date}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone={}",
        urlencoding::encode(tzp)
    );
    let resp: WeatherResp = ureq::get(&url)
        .call()
        .context("archive request failed")?
        .into_json()
        .context("invalid archive JSON")?;
    extract_one_day(resp, lat, lon, date, "archive")
}

fn extract_one_day(resp: WeatherResp, lat: f64, lon: f64, date: &str, source: &str) -> Result<FetchedWeather> {
    let daily = resp.daily.ok_or_else(|| anyhow!("No daily data returned"))?;
    let idx = daily
        .time
        .iter()
        .position(|t| t == date)
        .ok_or_else(|| anyhow!("Requested date not in response"))?;

    let tmax = *daily
        .temperature_2m_max
        .get(idx)
        .ok_or_else(|| anyhow!("missing tmax"))?;
    let tmin = *daily
        .temperature_2m_min
        .get(idx)
        .ok_or_else(|| anyhow!("missing tmin"))?;
    let prec = *daily.precipitation_sum.get(idx).unwrap_or(&0.0);

    Ok(FetchedWeather {
        timestamp: Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        city: String::new(),
        state: String::new(),
        date: date.to_string(),
        latitude: lat,
        longitude: lon,
        timezone: resp.timezone.unwrap_or_else(|| "auto".to_string()),
        source: source.to_string(),
        temp_max_c: tmax,
        temp_min_c: tmin,
        precipitation_mm: prec,
    })
}

// =============== Logging ==================

fn load_log() -> (Vec<FetchedWeather>, PathBuf) {
    let path = log_path();
    let mut entries = Vec::new();
    if path.exists() {
        if let Ok(file) = File::open(&path) {
            let reader = BufReader::new(file);
            for line in reader.lines().flatten() {
                if let Ok(e) = serde_json::from_str::<FetchedWeather>(&line) {
                    entries.push(e);
                }
            }
        }
    }
    (entries, path)
}

fn append_log(path: &Path, entry: &FetchedWeather) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).ok();
    }
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .with_context(|| format!("open log for append: {}", path.display()))?;
    writeln!(f, "{}", serde_json::to_string(entry)?).context("write log line")
}

fn export_csv(path: &Path, entries: &[FetchedWeather]) -> Result<PathBuf> {
    let csv_path = path.with_extension("csv");
    if let Some(parent) = csv_path.parent() {
        fs::create_dir_all(parent).ok();
    }
    let mut f = File::create(&csv_path)?;
    writeln!(f, "timestamp,city,state,date,latitude,longitude,timezone,source,temp_max_c,temp_min_c,precipitation_mm")?;
    for e in entries {
        writeln!(
            f,
            "{},{},{},{},{:.5},{:.5},{},{},{:.2},{:.2},{:.2}",
            e.timestamp,
            e.city,
            e.state,
            e.date,
            e.latitude,
            e.longitude,
            e.timezone,
            e.source,
            e.temp_max_c,
            e.temp_min_c,
            e.precipitation_mm
        )?;
    }
    Ok(csv_path)
}

fn clear_log(path: &Path) -> Result<()> {
    if path.exists() {
        fs::remove_file(path).ok();
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).ok();
    }
    File::create(path)?; // recreate empty
    Ok(())
}

fn log_path() -> PathBuf {
    if let Some(proj) = ProjectDirs::from("com", "you", "local_weather_app") {
        proj.data_dir().join("weather_log.jsonl")
    } else {
        PathBuf::from("weather_log.jsonl")
    }
}

// =============== State Mapping ==================

fn resolve_state_name(input: &str) -> String {
    let s = input.trim();
    if s.len() == 2 {
        if let Some(name) = us_state_long_name(s) {
            return name.to_string();
        }
    }
    // Title-case fallback
    let mut out = String::new();
    for (i, part) in s.split_whitespace().enumerate() {
        if i > 0 {
            out.push(' ');
        }
        let mut chars = part.chars();
        if let Some(c) = chars.next() {
            out.push(c.to_ascii_uppercase());
        }
        for c in chars {
            out.push(c.to_ascii_lowercase());
        }
    }
    out
}

fn us_state_long_name(code: &str) -> Option<&'static str> {
    let c = code.to_ascii_uppercase();
    Some(match c.as_str() {
        "AL" => "Alabama",
        "AK" => "Alaska",
        "AZ" => "Arizona",
        "AR" => "Arkansas",
        "CA" => "California",
        "CO" => "Colorado",
        "CT" => "Connecticut",
        "DE" => "Delaware",
        "FL" => "Florida",
        "GA" => "Georgia",
        "HI" => "Hawaii",
        "ID" => "Idaho",
        "IL" => "Illinois",
        "IN" => "Indiana",
        "IA" => "Iowa",
        "KS" => "Kansas",
        "KY" => "Kentucky",
        "LA" => "Louisiana",
        "ME" => "Maine",
        "MD" => "Maryland",
        "MA" => "Massachusetts",
        "MI" => "Michigan",
        "MN" => "Minnesota",
        "MS" => "Mississippi",
        "MO" => "Missouri",
        "MT" => "Montana",
        "NE" => "Nebraska",
        "NV" => "Nevada",
        "NH" => "New Hampshire",
        "NJ" => "New Jersey",
        "NM" => "New Mexico",
        "NY" => "New York",
        "NC" => "North Carolina",
        "ND" => "North Dakota",
        "OH" => "Ohio",
        "OK" => "Oklahoma",
        "OR" => "Oregon",
        "PA" => "Pennsylvania",
        "RI" => "Rhode Island",
        "SC" => "South Carolina",
        "SD" => "South Dakota",
        "TN" => "Tennessee",
        "TX" => "Texas",
        "UT" => "Utah",
        "VT" => "Vermont",
        "VA" => "Virginia",
        "WA" => "Washington",
        "WV" => "West Virginia",
        "WI" => "Wisconsin",
        "WY" => "Wyoming",
        "DC" => "District of Columbia",
        _ => return None,
    })
}
