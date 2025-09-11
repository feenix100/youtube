Option Explicit
Function ReverseGeocoder(lati As Double, longi As Double) As String
On Error GoTo 0
Dim xD As New MSXML2.DOMDocument
Dim URL As String, vbErr As String

'insert module copy this code snippet to add formula for ReverseGeocoder
'this will take a latitude and longitude location, access openstreetmapapi and return the location based on coordinates
'From the Tools tab of the VBA window, select References.
'You will see a list of available references in the References – VBAProject dialog box.
'Select Microsoft XML, v3.0, and make sure the reference is marked.

xD.async = False

URL = "https://nominatim.openstreetmap.org/reverse?lat=" + CStr(lati) + _
"&lon=" + CStr(longi)

xD.Load ("https://nominatim.openstreetmap.org/reverse?lat=" + CStr(lati) + _
"&lon=" + CStr(longi))

If xD.parseError.ErrorCode <> 0 Then
Application.Caller.Font.ColorIndex = vbErr

ReverseGeocoder = xD.parseError.reason
Else

xD.SetProperty "SelectionLanguage", "XPath"

Dim loca As MSXML2.IXMLDOMElement
Set loca = xD.SelectSingleNode(" / reversegeocode / result")

If loca Is Nothing Then
Application.Caller.Font.ColorIndex = vbErr

ReverseGeocoder = xD.XML
Else
Application.Caller.Font.ColorIndex = vbOK
ReverseGeocoder = loca.Text

End If

End If

Exit Function
0:
Debug.Print Err.Description
End Function