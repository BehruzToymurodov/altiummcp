// design_utils.pas
// Creating projects and documents, opening/saving them, and building PCB
// copper/outline geometry from a spec file. Added in the altium-mcp-unified
// fork; follows the conventions of the other units (results are JSON text,
// coordinates are mils, PCB coordinates relative to the board origin).
//
// API patterns used here are taken from widely used community scripts:
//   CreateNewFreeDocumentFromDocumentKind + DoSafeChangeFileNameAndSave
//   Project.DM_AddSourceDocument inside DM_BeginUpdate/DM_EndUpdate
//   TPolySegment + Segments[] + PointCount for polygons and the board outline

// ---------------------------------------------------------------- helpers

// Value of '"Name": value' in the request, '' when absent
function ReqValue(RequestData: TStringList; Name: String): String;
var
    i : Integer;
begin
    Result := '';
    for i := 0 to RequestData.Count - 1 do
        if (Pos('"' + Name + '"', RequestData[i]) > 0) then
        begin
            Result := Trim(JSONLineValue(RequestData[i]));
            Exit;
        end;
end;

function IsScriptPrj(Prj: IProject): Boolean;
begin
    Result := UpperCase(ExtractFileExt(Prj.DM_ProjectFullPath)) = '.PRJSCR';
end;

function DocKindFromPath(Path: String): String;
var
    Ext : String;
begin
    Ext := UpperCase(ExtractFileExt(Path));
    Result := '';
    if      (Ext = '.SCHDOC') then Result := 'SCH'
    else if (Ext = '.PCBDOC') then Result := 'PCB'
    else if (Ext = '.SCHLIB') then Result := 'SCHLIB'
    else if (Ext = '.PCBLIB') then Result := 'PCBLIB'
    else if (Ext = '.OUTJOB') then Result := 'OUTPUTJOB';
end;

function JStr(S: String): String;
begin
    Result := '"' + JSONEscapeString(S) + '"';
end;

function FindOpenProject(Path: String): IProject;
var
    P : Integer;
    Prj : IProject;
begin
    Result := nil;
    for P := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Prj := GetWorkspace.DM_Projects(P);
        if (Prj <> nil) and (UpperCase(Prj.DM_ProjectFullPath) = UpperCase(Path)) then
        begin
            Result := Prj;
            Exit;
        end;
    end;
end;

function OpenProjectFile(Path: String): IProject;
begin
    Result := FindOpenProject(Path);
    if (Result <> nil) then Exit;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', Path);
    RunProcess('WorkspaceManager:OpenObject');
    Sleep(500);
    Result := FindOpenProject(Path);
end;

// Show one of the project's documents so the project becomes focused
procedure FocusProject(Prj: IProject; PreferKind: String);
var
    I : Integer;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    Pick : String;
begin
    Pick := '';
    for I := 0 to Prj.DM_LogicalDocumentCount - 1 do
    begin
        Doc := Prj.DM_LogicalDocuments(I);
        if (Pick = '') then Pick := Doc.DM_FullPath;
        if (PreferKind <> '') and (DocKindFromPath(Doc.DM_FullPath) = PreferKind) then
        begin
            Pick := Doc.DM_FullPath;
            Break;
        end;
    end;
    if (Pick <> '') then
    begin
        ServerDoc := OpenLibraryDocument(DocKindFromPath(Pick), Pick);
        if (ServerDoc <> nil) then Client.ShowDocument(ServerDoc);
    end;
end;

// Create an empty document on disk. Returns '' or an error text.
function CreateDocumentFile(Kind: String; Path: String): String;
var
    Doc : IServerDocument;
begin
    Result := '';
    if FileExists(Path) then Exit;          // never overwrite a design
    ForceDirectories(ExtractFilePath(Path));
    Doc := CreateNewFreeDocumentFromDocumentKind(Kind, True);
    if (Doc = nil) then
    begin
        Result := 'could not create a new ' + Kind + ' document';
        Exit;
    end;
    if not Doc.DoSafeChangeFileNameAndSave(Path, Kind) then
    begin
        Result := 'could not save the new ' + Kind + ' document to ' + Path;
        Exit;
    end;
    // Close the free copy: the project opens its own when it loads
    try
        Client.CloseDocument(Doc);
    except
    end;
end;

function SaveProjectFile(Prj: IProject): Boolean;
var
    PrjDoc : IServerDocument;
begin
    Result := False;
    try
        PrjDoc := Client.GetDocumentByPath(Prj.DM_ProjectFullPath);
        if (PrjDoc <> nil) then
        begin
            PrjDoc.SetModified(True);
            Result := PrjDoc.DoFileSave('');
        end;
    except
        Result := False;
    end;
end;

// ------------------------------------------------- project / documents

// project_spec.txt:  PROJECT|<.PrjPcb path>   then   DOC|<kind>|<path>
// The .PrjPcb itself is written by the Python side before this runs.
function ExecuteCreateProject(SpecPath: String): String;
var
    Spec : TStringList;
    i : Integer;
    Rec, Kind, Path, PrjPath, Err, Created, Failed : String;
    Prj : IProject;
begin
    if not FileExists(SpecPath) then
    begin
        Result := 'ERROR: project spec not found';
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    PrjPath := '';
    Created := '';
    Failed := '';
    try
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            if (GetFieldFromPipeString(Rec, 0) = 'PROJECT') then
                PrjPath := GetFieldFromPipeString(Rec, 1)
            else if (GetFieldFromPipeString(Rec, 0) = 'DOC') then
            begin
                Kind := GetFieldFromPipeString(Rec, 1);
                Path := GetFieldFromPipeString(Rec, 2);
                Err := CreateDocumentFile(Kind, Path);
                if (Err = '') then
                begin
                    if (Created <> '') then Created := Created + ', ';
                    Created := Created + JStr(Path);
                end
                else
                begin
                    if (Failed <> '') then Failed := Failed + ', ';
                    Failed := Failed + JStr(Err);
                end;
            end;
        end;

        if (PrjPath = '') or not FileExists(PrjPath) then
        begin
            Result := 'ERROR: project file missing: ' + PrjPath;
            Exit;
        end;

        Prj := OpenProjectFile(PrjPath);
        if (Prj <> nil) then FocusProject(Prj, 'SCH');

        Result := '{"project": ' + JStr(PrjPath) +
                  ', "opened": ' + LowerCase(BoolToStr(Prj <> nil, True)) +
                  ', "documents_created": [' + Created + ']' +
                  ', "errors": [' + Failed + ']}';
    finally
        Spec.Free;
    end;
end;

// add_document: kind (SCH/PCB), path, add_to_project (true/false)
function ExecuteAddDocument(RequestData: TStringList): String;
var
    Kind, Path, Err : String;
    AddToProject, Added, Saved : Boolean;
    Prj : IProject;
    ServerDoc : IServerDocument;
begin
    Path := ReqValue(RequestData, 'path');
    Kind := UpperCase(ReqValue(RequestData, 'kind'));
    if (Kind = '') then Kind := DocKindFromPath(Path);
    AddToProject := (LowerCase(ReqValue(RequestData, 'add_to_project')) <> 'false');
    if (Path = '') or (Kind = '') then
    begin
        Result := 'ERROR: add_document needs a path ending in .SchDoc or .PcbDoc';
        Exit;
    end;
    if FileExists(Path) then
    begin
        Result := 'ERROR: file already exists (not overwritten): ' + Path;
        Exit;
    end;

    Err := CreateDocumentFile(Kind, Path);
    if (Err <> '') then
    begin
        Result := 'ERROR: ' + Err;
        Exit;
    end;

    Added := False;
    Saved := False;
    Prj := GetWorkspace.DM_FocusedProject;
    if AddToProject and (Prj <> nil) and not IsScriptPrj(Prj) and
       (Pos('Free Documents', Prj.DM_ProjectFileName) = 0) then
    begin
        Prj.DM_BeginUpdate;
        Prj.DM_AddSourceDocument(Path);
        Prj.DM_EndUpdate;
        Added := True;
        Saved := SaveProjectFile(Prj);
    end;

    ServerDoc := OpenLibraryDocument(Kind, Path);

    Result := '{"path": ' + JStr(Path) + ', "kind": ' + JStr(Kind) +
              ', "added_to_project": ' + LowerCase(BoolToStr(Added, True)) +
              ', "project_saved": ' + LowerCase(BoolToStr(Saved, True)) +
              ', "opened": ' + LowerCase(BoolToStr(ServerDoc <> nil, True));
    if Added then
        Result := Result + ', "project": ' + JStr(Prj.DM_ProjectFullPath);
    Result := Result + '}';
end;

// open_document: path of a .PrjPcb, .SchDoc, .PcbDoc, .SchLib, .PcbLib, .OutJob
function ExecuteOpenDocument(RequestData: TStringList): String;
var
    Path, Kind : String;
    Prj : IProject;
    ServerDoc : IServerDocument;
begin
    Path := ReqValue(RequestData, 'path');
    if not FileExists(Path) then
    begin
        Result := 'ERROR: file not found: ' + Path;
        Exit;
    end;
    if (UpperCase(ExtractFileExt(Path)) = '.PRJPCB') then
    begin
        Prj := OpenProjectFile(Path);
        if (Prj = nil) then
        begin
            Result := 'ERROR: Altium did not open the project ' + Path;
            Exit;
        end;
        FocusProject(Prj, UpperCase(ReqValue(RequestData, 'focus')));
        Result := '{"opened": ' + JStr(Path) + ', "kind": "PROJECT", "documents": ' +
                  IntToStr(Prj.DM_LogicalDocumentCount) + '}';
        Exit;
    end;
    Kind := DocKindFromPath(Path);
    if (Kind = '') then
    begin
        Result := 'ERROR: unsupported file type: ' + Path;
        Exit;
    end;
    ServerDoc := OpenLibraryDocument(Kind, Path);
    if (ServerDoc = nil) then
        Result := 'ERROR: Altium did not open ' + Path
    else
        Result := '{"opened": ' + JStr(Path) + ', "kind": ' + JStr(Kind) + '}';
end;

// save_documents: scope = "active" (default) or "project"
function ExecuteSaveDocuments(RequestData: TStringList): String;
var
    Scope, List : String;
    Prj : IProject;
    I, N : Integer;
    Doc : IDocument;
    ServerDoc : IServerDocument;
begin
    Scope := LowerCase(ReqValue(RequestData, 'scope'));
    List := '';
    N := 0;
    if (Scope = 'project') then
    begin
        Prj := GetWorkspace.DM_FocusedProject;
        if (Prj = nil) then
        begin
            Result := 'ERROR: No project is open';
            Exit;
        end;
        for I := 0 to Prj.DM_LogicalDocumentCount - 1 do
        begin
            Doc := Prj.DM_LogicalDocuments(I);
            ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
            if (ServerDoc <> nil) and ServerDoc.Modified then
                if ServerDoc.DoFileSave('') then
                begin
                    if (List <> '') then List := List + ', ';
                    List := List + JStr(Doc.DM_FullPath);
                    N := N + 1;
                end;
        end;
        if SaveProjectFile(Prj) then
        begin
            if (List <> '') then List := List + ', ';
            List := List + JStr(Prj.DM_ProjectFullPath);
            N := N + 1;
        end;
    end
    else
    begin
        ServerDoc := Client.CurrentView.OwnerDocument;
        if (ServerDoc = nil) then
        begin
            Result := 'ERROR: no active document';
            Exit;
        end;
        ServerDoc.SetModified(True);
        if ServerDoc.DoFileSave('') then
        begin
            List := JStr(ServerDoc.FileName);
            N := 1;
        end;
    end;
    Result := '{"saved_count": ' + IntToStr(N) + ', "saved": [' + List + ']}';
end;

// Compile the focused project and bring its PCB to the front, ready for
// Design > Import Changes. Returns the PCB path ('' when the project has none).
function ExecutePreparePcbUpdate(RequestData: TStringList): String;
var
    Prj : IProject;
    I : Integer;
    Doc : IDocument;
    PcbPath : String;
    ServerDoc : IServerDocument;
begin
    Prj := GetWorkspace.DM_FocusedProject;
    if (Prj = nil) or IsScriptPrj(Prj) then
    begin
        Result := 'ERROR: No design project is focused';
        Exit;
    end;
    Prj.DM_Compile;
    PcbPath := '';
    for I := 0 to Prj.DM_LogicalDocumentCount - 1 do
    begin
        Doc := Prj.DM_LogicalDocuments(I);
        if (DocKindFromPath(Doc.DM_FullPath) = 'PCB') then
        begin
            PcbPath := Doc.DM_FullPath;
            Break;
        end;
    end;
    if (PcbPath = '') then
    begin
        Result := 'ERROR: the project has no PcbDoc - add one with add_document first';
        Exit;
    end;
    ServerDoc := OpenLibraryDocument('PCB', PcbPath);
    Result := '{"project": ' + JStr(Prj.DM_ProjectFullPath) + ', "pcb": ' + JStr(PcbPath) +
              ', "pcb_opened": ' + LowerCase(BoolToStr(ServerDoc <> nil, True)) + '}';
end;

// ------------------------------------------------------- PCB geometry

function FindNetByName(Board: IPCB_Board; NetName: String): IPCB_Net;
var
    Iter : IPCB_BoardIterator;
    Net : IPCB_Net;
begin
    Result := nil;
    if (NetName = '') then Exit;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eNetObject));
    Iter.AddFilter_LayerSet(AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Net := Iter.FirstPCBObject;
    while (Net <> nil) do
    begin
        if (UpperCase(Net.Name) = UpperCase(NetName)) then
        begin
            Result := Net;
            Break;
        end;
        Net := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
end;

function FieldMil(Rec: String; Index: Integer): Integer;
begin
    Result := MilsToCoord(SafeStrToFloat(Trim(GetFieldFromPipeString(Rec, Index))));
end;

procedure AddToBoard(Board: IPCB_Board; Obj: IPCB_Primitive);
begin
    Board.AddPCBObject(Obj);
    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                                  PCBM_BoardRegisteration, Obj.I_ObjectAddress);
end;

// Board outline from OUTLINE|x|y|x|y|...  (absolute vertices, closes itself)
function SetBoardOutline(Board: IPCB_Board; Rec: String): Integer;
var
    BOL : IPCB_BoardOutline;
    PolySeg : TPolySegment;
    N, I, F : Integer;
begin
    N := 0;
    F := 1;
    while (Trim(GetFieldFromPipeString(Rec, F)) <> '') do
    begin
        N := N + 1;
        F := F + 2;
    end;
    Result := N;
    if (N < 3) then Exit;

    BOL := Board.BoardOutline;
    Board.BeginModify;
    BOL.BeginModify;
    BOL.PointCount := N;
    PolySeg := TPolySegment;
    PolySeg.Kind := ePolySegmentLine;
    for I := 0 to N do
    begin
        F := 1 + 2 * (I mod N);              // segment N closes on vertex 0
        PolySeg.vx := FieldMil(Rec, F) + Board.XOrigin;
        PolySeg.vy := FieldMil(Rec, F + 1) + Board.YOrigin;
        BOL.Segments[I] := PolySeg;
    end;
    BOL.SetState_CopperPourInvalid;
    BOL.Rebuild;
    BOL.Invalidate;
    BOL.EndModify;
    BOL.SetState_XSizeYSize;
    BOL.GraphicallyInvalidate;
    Board.UpdateBoardOutline;
    Board.EndModify;
end;

// POLY|layer|net|name|x|y|x|y|...
function AddPolygonPour(Board: IPCB_Board; Rec: String): Boolean;
var
    Poly : IPCB_Polygon;
    PolySeg : TPolySegment;
    N, I, F : Integer;
begin
    Result := False;
    N := 0;
    F := 4;
    while (Trim(GetFieldFromPipeString(Rec, F)) <> '') do
    begin
        N := N + 1;
        F := F + 2;
    end;
    if (N < 3) then Exit;

    PCBServer.SystemOptions.PolygonRepour := eAlwaysRepour;   // no repour dialog
    Poly := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
    Poly.Layer := String2Layer(Trim(GetFieldFromPipeString(Rec, 1)));
    Poly.Name := GetFieldFromPipeString(Rec, 3);
    Poly.PolyHatchStyle := ePolySolid;
    Poly.RemoveIslandsByArea := False;
    Poly.RemoveNarrowNecks := True;
    Poly.RemoveDead := True;
    Poly.PourOver := ePolygonPourOver_SameNet;
    Poly.AvoidObsticles := True;
    Poly.Net := FindNetByName(Board, Trim(GetFieldFromPipeString(Rec, 2)));
    Poly.PointCount := N;
    PolySeg := TPolySegment;
    PolySeg.Kind := ePolySegmentLine;
    for I := 0 to N do
    begin
        F := 4 + 2 * (I mod N);
        PolySeg.vx := FieldMil(Rec, F) + Board.XOrigin;
        PolySeg.vy := FieldMil(Rec, F + 1) + Board.YOrigin;
        Poly.Segments[I] := PolySeg;
    end;
    Board.AddPCBObject(Poly);
    Poly.SetState_CopperPourInvalid;
    Poly.Rebuild;
    Poly.CopperPourValidate;
    Result := True;
end;

// pcb_spec.txt, one record per line (mils, relative to the board origin):
//   OUTLINE|x|y|x|y|...                       board shape
//   TRACK|x1|y1|x2|y2|width|layer|net
//   ARC|cx|cy|radius|start_deg|end_deg|width|layer|net
//   VIA|x|y|size|hole|net[|low_layer|high_layer]
//   POLY|layer|net|name|x|y|x|y|...           copper pour
//   FILL|x1|y1|x2|y2|layer|net
//   TEXT|x|y|height|layer|text
function ExecuteBuildPcb(SpecPath: String): String;
var
    Spec : TStringList;
    Board : IPCB_Board;
    i, Tracks, Arcs, Vias, Polys, Fills, Texts, Outline : Integer;
    Rec, Kind, Errors, MissingNets, NetName : String;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Via : IPCB_Via;
    Fill : IPCB_Fill;
    Txt : IPCB_Text;
    Net : IPCB_Net;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;
    if not FileExists(SpecPath) then
    begin
        Result := 'ERROR: pcb spec not found';
        Exit;
    end;

    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Tracks := 0; Arcs := 0; Vias := 0; Polys := 0; Fills := 0; Texts := 0; Outline := 0;
    Errors := '';
    MissingNets := '';

    PCBServer.PreProcess;
    try
        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Kind := GetFieldFromPipeString(Rec, 0);
            NetName := '';
            Net := nil;
            try
                if (Kind = 'OUTLINE') then
                    Outline := SetBoardOutline(Board, Rec)

                else if (Kind = 'TRACK') then
                begin
                    Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                    Track.X1 := FieldMil(Rec, 1) + Board.XOrigin;
                    Track.Y1 := FieldMil(Rec, 2) + Board.YOrigin;
                    Track.X2 := FieldMil(Rec, 3) + Board.XOrigin;
                    Track.Y2 := FieldMil(Rec, 4) + Board.YOrigin;
                    Track.Width := FieldMil(Rec, 5);
                    Track.Layer := String2Layer(Trim(GetFieldFromPipeString(Rec, 6)));
                    NetName := Trim(GetFieldFromPipeString(Rec, 7));
                    Net := FindNetByName(Board, NetName);
                    if (Net <> nil) then Track.Net := Net;
                    AddToBoard(Board, Track);
                    Tracks := Tracks + 1;
                end

                else if (Kind = 'ARC') then
                begin
                    Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
                    Arc.XCenter := FieldMil(Rec, 1) + Board.XOrigin;
                    Arc.YCenter := FieldMil(Rec, 2) + Board.YOrigin;
                    Arc.Radius := FieldMil(Rec, 3);
                    Arc.StartAngle := SafeStrToFloat(Trim(GetFieldFromPipeString(Rec, 4)));
                    Arc.EndAngle := SafeStrToFloat(Trim(GetFieldFromPipeString(Rec, 5)));
                    Arc.LineWidth := FieldMil(Rec, 6);
                    Arc.Layer := String2Layer(Trim(GetFieldFromPipeString(Rec, 7)));
                    NetName := Trim(GetFieldFromPipeString(Rec, 8));
                    Net := FindNetByName(Board, NetName);
                    if (Net <> nil) then Arc.Net := Net;
                    AddToBoard(Board, Arc);
                    Arcs := Arcs + 1;
                end

                else if (Kind = 'VIA') then
                begin
                    Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
                    Via.X := FieldMil(Rec, 1) + Board.XOrigin;
                    Via.Y := FieldMil(Rec, 2) + Board.YOrigin;
                    Via.Size := FieldMil(Rec, 3);
                    Via.HoleSize := FieldMil(Rec, 4);
                    if (Trim(GetFieldFromPipeString(Rec, 6)) <> '') then
                    begin
                        Via.LowLayer := String2Layer(Trim(GetFieldFromPipeString(Rec, 6)));
                        Via.HighLayer := String2Layer(Trim(GetFieldFromPipeString(Rec, 7)));
                    end
                    else
                    begin
                        Via.LowLayer := eBottomLayer;
                        Via.HighLayer := eTopLayer;
                    end;
                    NetName := Trim(GetFieldFromPipeString(Rec, 5));
                    Net := FindNetByName(Board, NetName);
                    if (Net <> nil) then Via.Net := Net;
                    AddToBoard(Board, Via);
                    Vias := Vias + 1;
                end

                else if (Kind = 'POLY') then
                begin
                    NetName := Trim(GetFieldFromPipeString(Rec, 2));
                    Net := FindNetByName(Board, NetName);
                    if AddPolygonPour(Board, Rec) then Polys := Polys + 1;
                end

                else if (Kind = 'FILL') then
                begin
                    Fill := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
                    Fill.X1Location := FieldMil(Rec, 1) + Board.XOrigin;
                    Fill.Y1Location := FieldMil(Rec, 2) + Board.YOrigin;
                    Fill.X2Location := FieldMil(Rec, 3) + Board.XOrigin;
                    Fill.Y2Location := FieldMil(Rec, 4) + Board.YOrigin;
                    Fill.Rotation := 0;
                    Fill.Layer := String2Layer(Trim(GetFieldFromPipeString(Rec, 5)));
                    NetName := Trim(GetFieldFromPipeString(Rec, 6));
                    Net := FindNetByName(Board, NetName);
                    if (Net <> nil) then Fill.Net := Net;
                    AddToBoard(Board, Fill);
                    Fills := Fills + 1;
                end

                else if (Kind = 'TEXT') then
                begin
                    Txt := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
                    Txt.XLocation := FieldMil(Rec, 1) + Board.XOrigin;
                    Txt.YLocation := FieldMil(Rec, 2) + Board.YOrigin;
                    Txt.Size := FieldMil(Rec, 3);
                    Txt.Width := MilsToCoord(Round(CoordToMils(Txt.Size) / 8) + 1);
                    Txt.Layer := String2Layer(Trim(GetFieldFromPipeString(Rec, 4)));
                    Txt.Text := GetFieldFromPipeString(Rec, 5);
                    AddToBoard(Board, Txt);
                    Texts := Texts + 1;
                end;

                if (NetName <> '') and (Net = nil) and (Pos('"' + NetName + '"', MissingNets) = 0) then
                begin
                    if (MissingNets <> '') then MissingNets := MissingNets + ', ';
                    MissingNets := MissingNets + JStr(NetName);
                end;
            except
                if (Errors <> '') then Errors := Errors + ', ';
                Errors := Errors + JStr('line ' + IntToStr(i + 1) + ' (' + Kind + ') failed');
            end;
        end;
    finally
        PCBServer.PostProcess;
        Spec.Free;
    end;

    Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);
    Result := '{"outline_vertices": ' + IntToStr(Outline) +
              ', "tracks": ' + IntToStr(Tracks) + ', "arcs": ' + IntToStr(Arcs) +
              ', "vias": ' + IntToStr(Vias) + ', "polygons": ' + IntToStr(Polys) +
              ', "fills": ' + IntToStr(Fills) + ', "texts": ' + IntToStr(Texts) +
              ', "nets_not_found": [' + MissingNets + ']' +
              ', "errors": [' + Errors + ']}';
end;

// Violations the online DRC has already marked on the board
function ExecuteGetPcbViolations(RequestData: TStringList): String;
var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    V : IPCB_Violation;
    List : String;
    N, Limit : Integer;
begin
    Board := GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;
    Limit := 200;
    if (ReqValue(RequestData, 'limit') <> '') then
        Limit := StrToInt(ReqValue(RequestData, 'limit'));
    List := '';
    N := 0;
    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eViolationObject));
    Iter.AddFilter_LayerSet(AllLayers);
    V := Iter.FirstPCBObject;
    while (V <> nil) do
    begin
        if (N < Limit) then
        begin
            if (List <> '') then List := List + ', ';
            List := List + '{"rule": ' + JStr(V.Name) + ', "description": ' + JStr(V.Description) + '}';
        end;
        N := N + 1;
        V := Iter.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iter);
    Result := '{"violation_count": ' + IntToStr(N) + ', "violations": [' + List + ']}';
end;
