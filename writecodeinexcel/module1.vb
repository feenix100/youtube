'attach macro to button on main sheet
Sub ClearA14()
    ' Clear the contents of cell A14
    Range("A14").ClearContents
End Sub

Sub ShowSearchForm()
    UserForm1.Show
End Sub

'Search form, search for a number, makes a list of numbers, creates a sheet with the numbers 

Private Sub btnExportResults_Click()
    ' Call the ExportResultsToSheet subroutine
    ExportResultsToSheet
End Sub

Private Sub FrameResults_Click()

End Sub

Private Sub UserForm_Initialize()
    Me.txtSearchNumber.Font.Size = 16 ' Change 16 to your desired font size
    Me.txtSearchNumber.SetFocus ' Set focus to the text box when the form initializes
    Me.FrameResults.ScrollBars = fmScrollBarsVertical ' Enable vertical scrolling
    Me.FrameResults.Caption = "" ' Remove the frame caption
End Sub

Private Sub txtSearchNumber_KeyDown(ByVal KeyCode As MSForms.ReturnInteger, ByVal Shift As Integer)
    If KeyCode = 13 Then ' 13 is the key code for Enter
        Call searchNumber
        HighlightSearchBox ' Highlight the text box content
    End If
End Sub

Private Sub cmdClose_Click()
    Unload Me
End Sub

Private Sub txtSearchNumber_LostFocus()
    Me.txtSearchNumber.SetFocus ' Ensure text box always stays in focus
End Sub

Private Sub searchNumber()
    Dim ws As Worksheet
    Dim searchNumber As String
    Dim foundCell As Range
    Dim firstAddress As String
    Dim colHeader As String
    Dim resultString As String
    Dim lblResult As MSForms.Label
    Dim topPosition As Single
    Dim frameContentHeight As Single
    Dim colorToggle As Boolean ' Variable to toggle color

    ' Determine the current top position in the frame
    If Me.FrameResults.Controls.Count > 0 Then
        topPosition = Me.FrameResults.Controls(Me.FrameResults.Controls.Count - 1).Top + _
                      Me.FrameResults.Controls(Me.FrameResults.Controls.Count - 1).Height + 5
    Else
        topPosition = 0
    End If

    ' Get the search number from the textbox
    searchNumber = Me.txtSearchNumber.Value

    ' Validate user input
    If IsNumeric(searchNumber) Then
        ' Convert the input to a number
        searchNumber = CLng(searchNumber)

        ' Loop through the specified sheet and columns to search for the number
        Set ws = ThisWorkbook.Sheets("Sheet1") ' Change "Sheet1" to your sheet name
        With ws.Range("B2:Z350") ' Change "B2:Z350" to your data range, assuming headers are in the first row
            Set foundCell = .Find(What:=searchNumber, LookIn:=xlValues, LookAt:=xlWhole, MatchCase:=False)
            If Not foundCell Is Nothing Then
                firstAddress = foundCell.Address
                Do
                    colHeader = ws.Cells(1, foundCell.Column).Value
                    resultString = "Found " & foundCell.Value & " on " & colHeader

                    ' Create a new label for each result
                    Set lblResult = Me.FrameResults.Controls.Add("Forms.Label.1")
                    lblResult.Caption = resultString
                    lblResult.Top = topPosition
                    lblResult.Left = 10
                    lblResult.Width = Me.FrameResults.Width - 20
                    If colorToggle Then
                        lblResult.ForeColor = RGB(0, 0, 255) ' Blue color
                    Else
                        lblResult.ForeColor = RGB(0, 0, 0) ' Black color
                    End If
                    colorToggle = Not colorToggle ' Toggle color
                    lblResult.Font.Size = 14 ' Change the font size to desired value
                    lblResult.Font.Bold = True ' Make the font bold
                    topPosition = topPosition + lblResult.Height + 5

                    Set foundCell = .FindNext(foundCell)
                Loop While Not foundCell Is Nothing And foundCell.Address <> firstAddress
            Else
                Set lblResult = Me.FrameResults.Controls.Add("Forms.Label.1")
                lblResult.Caption = "Number " & searchNumber & " not found."
                lblResult.Top = topPosition
                lblResult.Left = 10
                lblResult.Width = Me.FrameResults.Width - 20
                lblResult.ForeColor = RGB(255, 0, 0) ' Red color for not found message
                lblResult.Font.Size = 14 ' Change the font size to desired value
                lblResult.Font.Bold = True ' Make the font bold
                topPosition = topPosition + lblResult.Height + 5
            End If
        End With

        ' Adjust the scroll height to fit all labels
        frameContentHeight = topPosition + 10 ' Add some padding
        Me.FrameResults.ScrollHeight = frameContentHeight
        HighlightSearchBox ' Highlight the text box content
    Else
        MsgBox "Please enter a valid number.", vbExclamation
        HighlightSearchBox ' Highlight the text box content
    End If
End Sub

Private Sub HighlightSearchBox()
    ' Highlight the text in txtSearchNumber
    Me.txtSearchNumber.SelStart = 0 ' Start at the beginning of the text
    Me.txtSearchNumber.SelLength = Len(Me.txtSearchNumber.Text) ' Select the entire text
End Sub

Private Sub ExportResultsToSheet()
    Dim wsNew As Worksheet
    Dim lbl As MSForms.Label
    Dim i As Integer
    Dim sheetName As String

    ' Create a new worksheet with today's date as the name
    sheetName = "Results " & Format(Date, "yyyy-mm-dd")
    Set wsNew = ThisWorkbook.Sheets.Add
    On Error Resume Next
    wsNew.Name = sheetName
    If Err.Number <> 0 Then
        MsgBox "A sheet with today's date already exists. Results will be exported to a new sheet with a unique name.", vbExclamation
        wsNew.Name = sheetName & " (" & wsNew.Index & ")"
        Err.Clear
    End If
    On Error GoTo 0

    ' Set headers in the new sheet
    wsNew.Range("A1").Value = "Search Results"
    wsNew.Range("A1").Font.Bold = True
    wsNew.Range("A1").Font.Size = 14

    ' Loop through labels in FrameResults and copy to new sheet
    For i = 0 To Me.FrameResults.Controls.Count - 1
        Set lbl = Me.FrameResults.Controls(i)
        wsNew.Cells(i + 3, 1).Value = lbl.Caption
        wsNew.Cells(i + 3, 1).Font.Size = 12
        wsNew.Cells(i + 3, 1).Font.Color = RGB(0, 0, 0) ' Adjust text color if needed
    Next i

    ' Optional: AutoFit columns for better readability
    wsNew.Columns("A").AutoFit

    MsgBox "Results exported to a new sheet.", vbInformation
End Sub


Sub AddClearButton()
    Dim ws As Worksheet
    Dim btn As Button
    
    ' Reference to the active sheet
    Set ws = ActiveSheet
    
    ' Add a button to clear the contents of A14
    Set btn = ws.Buttons.Add(150, 10, 100, 30) ' Adjust the position and size as needed
    With btn
        .Caption = "Clear A14"
        .OnAction = "ClearA14"
    End With
End Sub

'this code runs inside a form to display the header of a column based on search number
Function FindHeaderByLookupValue(lookupValue As Variant, lookupRange As Range, headerRange As Range) As Variant
    Dim cell As Range
    Dim col As Range
    Dim foundExactMatch As Boolean
    
    ' Loop through each column in the lookup range
    For Each col In lookupRange.Columns
        foundExactMatch = False
        
        ' Loop through each cell in the current column
        For Each cell In col.Cells
            ' Check for exact match of value
            If cell.Value = lookupValue And Len(cell.Value) = Len(lookupValue) Then
                foundExactMatch = True
                Exit For
            End If
        Next cell
        
        ' If an exact match is found, return the corresponding header value
        If foundExactMatch Then
            FindHeaderByLookupValue = headerRange.Cells(1, col.Column - lookupRange.Column + 1).Value
            Exit Function
        End If
    Next col
    
    FindHeaderByLookupValue = "Item Not Found"
End Function

