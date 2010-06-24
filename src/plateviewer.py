from cpatool import CPATool
from colorbarpanel import ColorBarPanel
from dbconnect import DBConnect, UniqueImageClause, image_key_columns
import platemappanel as pmp
from wx.combo import OwnerDrawnComboBox as ComboBox
import imagetools
import properties
import logging
import matplotlib.cm
import numpy as np
import os
import sys
import re
import wx

p = properties.Properties.getInstance()
# Hack the properties module so it doesn't require the object table.
properties.optional_vars += ['object_table']
db = DBConnect.getInstance()

P96   = (8, 12)
P384  = (16, 24)
P1536 = (32, 48)
P5600 = (40, 140)

required_fields = ['plate_type', 'plate_id', 'well_id']

class AwesomePMP(pmp.PlateMapPanel):
    '''
    PlateMapPanel that does selection and tooltips for data.
    '''
    def __init__(self, parent, data, shape=None, well_keys=None,
                 colormap='jet', well_disp=pmp.ROUNDED, row_label_format=None, **kwargs):
        pmp.PlateMapPanel.__init__(self, parent, data, shape, well_keys, 
                               colormap, well_disp, row_label_format,  **kwargs)

        self.chMap = p.image_channel_colors

        self.plate = None
        self.tip = wx.ToolTip('')
        self.tip.Enable(False)
        self.SetToolTip(self.tip)

        self.Bind(wx.EVT_LEFT_UP, self.OnClick)
        self.Bind(wx.EVT_MOTION, self.OnMotion)
        self.Bind(wx.EVT_LEFT_DCLICK, self.OnDClick)
        self.Bind(wx.EVT_RIGHT_UP, self.OnRClick)

    def SetPlate(self, plate):
        self.plate = plate

    def OnMotion(self, evt):
        well = self.GetWellAtCoord(evt.X, evt.Y)
        wellLabel = self.GetWellLabelAtCoord(evt.X, evt.Y)
        if well is not None and wellLabel is not None:
            self.tip.SetTip('%s: %s'%(wellLabel,self.data[well]))
            self.tip.Enable(True)
        else:
            self.tip.Enable(False)

    def OnClick(self, evt):
        well = self.GetWellAtCoord(evt.X, evt.Y)
        if well is not None:
            if evt.ShiftDown():
                self.ToggleSelected(well)
            else:
                self.SelectWell(well)

    def OnDClick(self, evt):
        if self.plate is not None:
            well = self.GetWellLabelAtCoord(evt.X, evt.Y)
            imKeys = db.execute('SELECT %s FROM %s WHERE %s="%s" AND %s="%s"'%
                                (UniqueImageClause(), p.image_table, p.well_id, well, p.plate_id, self.plate), silent=False)
            for imKey in imKeys:
                try:
                    imagetools.ShowImage(imKey, self.chMap, parent=self)
                except Exception, e:
                    logging.error('Could not open image: %s'%(e))

    def OnRClick(self, evt):
        if self.plate is not None:
            well = self.GetWellLabelAtCoord(evt.X, evt.Y)
            imKeys = db.execute('SELECT %s FROM %s WHERE %s="%s" AND %s="%s"'%
                                (UniqueImageClause(), p.image_table, p.well_id, well, p.plate_id, self.plate))
            self.ShowPopupMenu(imKeys, (evt.X,evt.Y))

    def ShowPopupMenu(self, items, pos):
        self.popupItemById = {}
        popupMenu = wx.Menu()
        popupMenu.SetTitle('Show Image')
        for item in items:
            id = wx.NewId()
            self.popupItemById[id] = item
            popupMenu.Append(id,str(item))
        popupMenu.Bind(wx.EVT_MENU, self.OnSelectFromPopupMenu)
        self.PopupMenu(popupMenu, pos)

    def OnSelectFromPopupMenu(self, evt):
        """Handles selections from the popup menu."""
        imKey = self.popupItemById[evt.GetId()]
        imagetools.ShowImage(imKey, self.chMap, parent=self)



ID_EXIT = wx.NewId()

class PlateViewer(wx.Frame, CPATool):
    def __init__(self, parent, size=(800,-1), **kwargs):
        wx.Frame.__init__(self, parent, -1, size=size, title='Plate Viewer', **kwargs)
        CPATool.__init__(self)
        self.SetName(self.tool_name)

        # Check for required properties fields.
        fail = False
        for field in required_fields:
            if not p.field_defined(field):
                raise 'Properties field "%s" is required for PlateViewer.'%(field)
                fail = True
        if fail:    
            self.Destroy()
            return

        assert (p.well_id is not None and p.plate_id is not None), \
               'Plate Viewer requires the well_id and plate_id columns to be defined in your properties file.'

        self.chMap = p.image_channel_colors[:]

        self.Center()        
        self.menuBar = wx.MenuBar()
        self.SetMenuBar(self.menuBar)
        self.fileMenu = wx.Menu()
        self.loadCSVMenuItem = self.fileMenu.Append(-1, text='Load CSV\tCtrl+O', help='Load a CSV file storing per-image data')
        self.fileMenu.AppendSeparator()
        self.exitMenuItem = self.fileMenu.Append(id=ID_EXIT, text='Exit\tCtrl+Q',help='Close Plate Viewer')
        self.GetMenuBar().Append(self.fileMenu, 'File')

        self.Bind(wx.EVT_MENU, self.OnLoadCSV, self.loadCSVMenuItem)
        wx.EVT_MENU(self, ID_EXIT, lambda(_):self.Close())

        self.plateNames = db.GetPlateNames()

        dataSourceSizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Source:'), wx.VERTICAL)
        dataSourceSizer.Add(wx.StaticText(self, label='Data source:'))
        src_choices = [p.image_table]
        if p.object_table:
            src_choices += [p.object_table]
        try:
            db.execute('SELECT * FROM __Classifier_output LIMIT 1')
            src_choices += ['__Classifier_output']
        except:
            pass
        self.sourceChoice = ComboBox(self, choices=src_choices, style=wx.CB_READONLY)
        self.sourceChoice.Select(0)
        dataSourceSizer.Add(self.sourceChoice)

        dataSourceSizer.AddSpacer((-1,10))
        dataSourceSizer.Add(wx.StaticText(self, label='Measurement:'))
        measurements = get_non_blob_types_from_table(p.image_table)
        self.measurementsChoice = ComboBox(self, choices=measurements, style=wx.CB_READONLY)
        self.measurementsChoice.Select(0)
        dataSourceSizer.Add(self.measurementsChoice)

        groupingSizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Data aggregation:'), wx.VERTICAL)
        groupingSizer.Add(wx.StaticText(self, label='Aggregation method:'))
        aggregation = ['mean', 'sum', 'median', 'stdev', 'cv%', 'min', 'max']
        self.aggregationMethodsChoice = ComboBox(self, choices=aggregation, style=wx.CB_READONLY)
        self.aggregationMethodsChoice.Select(0)
        groupingSizer.Add(self.aggregationMethodsChoice)

        viewSizer = wx.StaticBoxSizer(wx.StaticBox(self, label='View options:'), wx.VERTICAL)
        viewSizer.Add(wx.StaticText(self, label='Color map:'))
        maps = [m for m in matplotlib.cm.datad.keys() if not m.endswith("_r")]
        maps.sort()
        self.colorMapsChoice = ComboBox(self, choices=maps, style=wx.CB_READONLY)
        self.colorMapsChoice.SetSelection(maps.index('jet'))
        viewSizer.Add(self.colorMapsChoice)

        viewSizer.AddSpacer((-1,10))
        viewSizer.Add(wx.StaticText(self, label='Well display:'))
        if p.image_thumbnail_cols:
            choices = pmp.all_well_shapes
        else:
            choices = list(pmp.all_well_shapes)
            choices.remove(pmp.THUMBNAIL)
        self.wellDisplayChoice = ComboBox(self, choices=choices, style=wx.CB_READONLY)
        self.wellDisplayChoice.Select(0)
        viewSizer.Add(self.wellDisplayChoice)

        viewSizer.AddSpacer((-1,10))
        viewSizer.Add(wx.StaticText(self, label='Number of plates:'))
        self.numberOfPlatesTE = wx.TextCtrl(self, -1, '1', style=wx.TE_PROCESS_ENTER)
        viewSizer.Add(self.numberOfPlatesTE)

        controlSizer = wx.BoxSizer(wx.VERTICAL)
        controlSizer.Add(dataSourceSizer, 0, wx.EXPAND)
        controlSizer.AddSpacer((-1,10))
        controlSizer.Add(groupingSizer, 0, wx.EXPAND)
        controlSizer.AddSpacer((-1,10))
        controlSizer.Add(viewSizer, 0, wx.EXPAND)

        self.plateMapSizer = wx.GridSizer(1,1,5,5)
        self.plateMaps = []
        self.plateMapChoices = []

        self.rightSizer = wx.BoxSizer(wx.VERTICAL)
        self.rightSizer.Add(self.plateMapSizer, 1, wx.EXPAND|wx.BOTTOM, 10)
        self.colorBar = ColorBarPanel(self, 'jet', size=(-1,25))
        self.rightSizer.Add(self.colorBar, 0, wx.EXPAND|wx.ALIGN_CENTER_HORIZONTAL)

        mainSizer = wx.BoxSizer(wx.HORIZONTAL)
        mainSizer.Add(controlSizer, 0, wx.LEFT|wx.TOP|wx.BOTTOM, 10)
        mainSizer.Add(self.rightSizer, 1, wx.EXPAND|wx.ALL, 10)

        self.SetSizer(mainSizer)
        self.SetClientSize((self.Size[0],self.Sizer.CalcMin()[1]))

        self.sourceChoice.Bind(wx.EVT_COMBOBOX, self.UpdateMeasurementChoice)
        self.measurementsChoice.Bind(wx.EVT_COMBOBOX, self.OnSelectMeasurement)
        self.aggregationMethodsChoice.Bind(wx.EVT_COMBOBOX, self.OnSelectAggregationMethod)
        self.colorMapsChoice.Bind(wx.EVT_COMBOBOX, self.OnSelectColorMap)
        self.numberOfPlatesTE.Bind(wx.EVT_TEXT_ENTER, self.OnEnterNumberOfPlates)
        self.wellDisplayChoice.Bind(wx.EVT_COMBOBOX, self.OnSelectWellDisplay)

        global_extents = db.execute('SELECT MIN(%s), MAX(%s) FROM %s'%(self.measurementsChoice.GetStringSelection(), 
                                                                       self.measurementsChoice.GetStringSelection(), 
                                                                       self.sourceChoice.GetStringSelection()))[0]
        self.colorBar.SetGlobalExtents(global_extents)
        self.AddPlateMap()
        self.UpdatePlateMaps()

    def OnLoadCSV(self, evt):
        dlg = wx.FileDialog(self, "Select a comma-separated-values file to load...",
                            defaultDir=os.getcwd(), style=wx.OPEN|wx.FD_CHANGE_DIR)
        if dlg.ShowModal() == wx.ID_OK:
            filename = dlg.GetPath()
            self.LoadCSV(filename)

    def LoadCSV(self, filename):
        countsTable = os.path.splitext(os.path.split(filename)[1])[0]
        if db.CreateTempTableFromCSV(filename, countsTable):
            self.AddTableChoice(countsTable)

    def AddTableChoice(self, table):
        sel = self.sourceChoice.GetSelection()
        self.sourceChoice.SetItems(self.sourceChoice.GetItems()+[table])
        self.sourceChoice.Select(sel)

    def AddPlateMap(self, plateIndex=0):
        '''
        Adds a new blank plateMap to the PlateMapSizer.
        '''
        data = np.ones(int(p.plate_type))
        if p.plate_type == '384': shape = P384
        elif p.plate_type == '96': shape = P96
        elif p.plate_type == '5600': shape = P5600
        elif p.plate_type == '1536': shape = P1536

        # Try to get explicit labels for all wells, otherwise the PMP
        # will generate labels automatically which MAY NOT MATCH labels
        # in the database, and therefore, showing images will not work. 
        res = db.execute('SELECT %s FROM %s WHERE %s!="" GROUP BY %s'%
                         (p.well_id, p.image_table, p.well_id, p.well_id))

        self.plateMapChoices += [ComboBox(self, choices=self.plateNames, 
                                          style=wx.CB_READONLY, size=(100,-1))]
        self.plateMapChoices[-1].Select(plateIndex)
        self.plateMapChoices[-1].Bind(wx.EVT_COMBOBOX, self.OnSelectPlate)

        well_keys = [(self.plateMapChoices[-1].GetString(plateIndex), r[0]) for r in res]
        if len(well_keys) != len(data):
            well_keys = None


        self.plateMaps += [AwesomePMP(self, data, shape, well_keys=well_keys,
                                      colormap=self.colorMapsChoice.GetStringSelection(),
                                      well_disp=self.wellDisplayChoice.GetStringSelection())]

        plateMapChoiceSizer = wx.BoxSizer(wx.HORIZONTAL)
        plateMapChoiceSizer.Add(wx.StaticText(self, label='Plate:'), 0, wx.EXPAND)
        plateMapChoiceSizer.Add(self.plateMapChoices[-1])

        singlePlateMapSizer = wx.BoxSizer(wx.VERTICAL)
        singlePlateMapSizer.Add(plateMapChoiceSizer, 0, wx.ALIGN_CENTER)
        singlePlateMapSizer.Add(self.plateMaps[-1], 1, wx.EXPAND|wx.ALIGN_CENTER)

        self.plateMapSizer.Add(singlePlateMapSizer, 1, wx.EXPAND|wx.ALIGN_CENTER)

    def UpdatePlateMaps(self):
        measurement = self.measurementsChoice.GetStringSelection()
        table       = self.sourceChoice.GetStringSelection()
        aggMethod   = self.aggregationMethodsChoice.GetStringSelection()
        table = self.sourceChoice.GetStringSelection()
        numeric_measurements = get_numeric_columns_from_table(table)
        categorical = measurement not in numeric_measurements
        self.colorBar.ClearNotifyWindows()

        if not categorical:
            if aggMethod == 'mean':
                expression = "AVG(%s.%s)"%(table, measurement)
            elif aggMethod == 'stdev':
                expression = "STDDEV(%s.%s)"%(table, measurement)
            elif aggMethod == 'cv%':
                expression = "STDDEV(%s.%s)/AVG(%s.%s)*100"%(table, measurement, table, measurement)
            elif aggMethod == 'sum':
                expression = "SUM(%s.%s)"%(table, measurement)
            elif aggMethod == 'min':
                expression = "MIN(%s.%s)"%(table, measurement)
            elif aggMethod == 'max':
                expression = "MAX(%s.%s)"%(table, measurement)
            elif aggMethod == 'median':
                expression = "MEDIAN(%s.%s)"%(table, measurement)

            if table == p.image_table:
                group = True
                platesWellsAndVals = db.execute('SELECT %s, %s, %s FROM %s %s'%
                                                (p.plate_id, p.well_id, expression, table,
                                                 'GROUP BY %s, %s'%(p.plate_id, p.well_id) if group else ''))
            elif set([p.well_id, p.plate_id]) == set(db.GetLinkingColumnsForTable(table)):
                # For data from tables with well and plate columns, we simply
                # fetch and aggregate
                # XXX: SHOULD we allow aggregation of per-well data since there 
                #      should logically only be one row per well???? 
                group = True
                platesWellsAndVals = db.execute('SELECT %s, %s, %s FROM %s %s'%
                                                (p.plate_id, p.well_id, expression, table,
                                                 'GROUP BY %s, %s'%(p.plate_id, p.well_id) if group else ''))
            else:
                # For data from other tables without well and plate columns, we 
                # need to link the table to the per image table via the 
                # ImageNumber column
                group = True
                platesWellsAndVals = db.execute('SELECT %s.%s, %s.%s, %s FROM %s, %s WHERE %s %s'%
                                                (p.image_table, p.plate_id, p.image_table, p.well_id, expression, 
                                                 p.image_table, table, 
                                                 ' AND '.join(['%s.%s=%s.%s'%(table, id, p.image_table, id) for id in db.GetLinkingColumnsForTable(table)]),
                                                 'GROUP BY %s.%s, %s.%s'%(p.image_table, p.plate_id, p.image_table, p.well_id) if group else ''))
            platesWellsAndVals = np.array(platesWellsAndVals, dtype=object)

            # Replace None's with nan
            for row in platesWellsAndVals:
                if row[2] is None:
                    row[2] = np.nan
            gmin = np.nanmin([float(val) for _,_,val in platesWellsAndVals])
            gmax = np.nanmax([float(val) for _,_,val in platesWellsAndVals])

            data = []
            dmax = -np.inf
            dmin = np.inf
            for plateChoice, plateMap in zip(self.plateMapChoices, self.plateMaps):
                plate = plateChoice.GetStringSelection()
                plateMap.SetPlate(plate)
                self.colorBar.AddNotifyWindow(plateMap)
                keys_and_vals = [v for v in platesWellsAndVals if str(v[0])==plate]
                platedata, platelabels = FormatPlateMapData(keys_and_vals)
                data += [platedata]
                dmin = np.nanmin([float(kv[-1]) for kv in keys_and_vals]+[dmin])
                dmax = np.nanmax([float(kv[-1]) for kv in keys_and_vals]+[dmax])

            if np.isinf(dmin) or np.isinf(dmax):
                dlg = wx.MessageDialog(self, 'No numeric data was found in this column ("%s.%s") for the selected plate ("%s").'%(table,measurement,plate), 'No data!', style=wx.OK)
                dlg.ShowModal()
                gmin = gmax = dmin = dmax = 1.

            self.colorBar.SetLocalExtents([dmin,dmax])
            self.colorBar.SetGlobalExtents([gmin,gmax])

        else:
            # CATEGORICAL data
            if table == p.image_table:
                platesWellsAndVals = db.execute('SELECT %s, %s, %s FROM %s %s'%
                                                (p.plate_id, p.well_id, measurement, table,
                                                 'GROUP BY %s, %s'%(p.plate_id, p.well_id)))
            elif set([p.well_id, p.plate_id]) == set(db.GetLinkingColumnsForTable(table)):
                # For data from tables with well and plate columns
                platesWellsAndVals = db.execute('SELECT %s, %s, %s FROM %s %s'%
                                                (p.plate_id, p.well_id, measurement, table,
                                                 'GROUP BY %s, %s'%(p.plate_id, p.well_id)))
            else:
                # For data from other tables without well and plate columns, we 
                # need to link the table to the per image table via the 
                # ImageNumber column
                platesWellsAndVals = db.execute('SELECT %s.%s, %s.%s, %s FROM %s, %s WHERE %s %s'%
                                                (p.image_table, p.plate_id, p.image_table, p.well_id, measurement, 
                                                 p.image_table, table, 
                                                 ' AND '.join(['%s.%s=%s.%s'%(table, id, p.image_table, id) for id in db.GetLinkingColumnsForTable(table)]),
                                                 'GROUP BY %s.%s, %s.%s'%(p.image_table, p.plate_id, p.image_table, p.well_id)))

            data = []
            for plateChoice, plateMap in zip(self.plateMapChoices, self.plateMaps):
                plate = plateChoice.GetStringSelection()
                plateMap.SetPlate(plate)
                self.colorBar.AddNotifyWindow(plateMap)
                keys_and_vals = [v for v in platesWellsAndVals if str(v[0])==plate]
                platedata, platelabels = FormatPlateMapData(keys_and_vals, categorical=True)
                data += [platedata]

            self.colorBar.SetLocalExtents([0,1])
            self.colorBar.SetGlobalExtents([0,1])

        for d, plateMap in zip(data, self.plateMaps):
            plateMap.SetWellKeys(platelabels)
            if categorical:
                plateMap.SetData(np.ones(d.shape) * np.nan)
                plateMap.SetTextData(d)
            else:
                plateMap.SetData(d, data_range=self.colorBar.GetLocalExtents(), 
                                 clip_interval=self.colorBar.GetLocalInterval(), 
                                 clip_mode=self.colorBar.GetClipMode())

    def UpdateMeasurementChoice(self, evt=None):
        '''
        Handles the selection of a source table (per-image or per-object) from
        a choice box.  The measurement choice box is populated with the names
        of numeric columns from the selected table.
        '''
        table = self.sourceChoice.GetStringSelection()
        self.measurementsChoice.SetItems(get_numeric_columns_from_table(table))
        self.measurementsChoice.Select(0)
        self.colorBar.ResetInterval()
        self.UpdatePlateMaps()

    def OnSelectPlate(self, evt):
        ''' Handles the selection of a plate from the plate choice box. '''
        self.UpdatePlateMaps()

    def OnSelectMeasurement(self, evt=None):
        ''' Handles the selection of a measurement to plot from a choice box. '''
        selected_measurement = self.measurementsChoice.GetStringSelection() 
        table = self.sourceChoice.GetStringSelection()
        numeric_measurements = get_numeric_columns_from_table(table)
        if selected_measurement not in numeric_measurements:
            self.aggregationMethodsChoice.Disable()
            self.colorMapsChoice.Disable()
        else:
            self.aggregationMethodsChoice.Enable()
            self.colorMapsChoice.Enable()
        self.colorBar.ResetInterval()
        self.UpdatePlateMaps()

    def OnSelectAggregationMethod(self, evt=None):
        ''' Handles the selection of an aggregation method from the choice box. '''
        self.colorBar.ResetInterval()
        self.UpdatePlateMaps()

    def OnSelectColorMap(self, evt=None):
        ''' Handles the selection of a color map from a choice box. '''
        map = self.colorMapsChoice.GetStringSelection()
        cm = matplotlib.cm.get_cmap(map)

        self.colorBar.SetMap(map)
        for plateMap in self.plateMaps:
            plateMap.SetColorMap(map)

    def OnSelectWellDisplay(self, evt=None):
        ''' Handles the selection of a well display choice from a choice box. '''
        sel = self.wellDisplayChoice.GetStringSelection()
        if sel.lower() == 'image':
            dlg = wx.MessageDialog(self, 
                'This mode will render each well as a shrunken image loaded '
                'from that well. This feature is currently VERY SLOW since it '
                'requires loading hundreds of full sized images. Are you sure '
                'you want to continue?',
                'Load all images?', wx.OK|wx.CANCEL|wx.ICON_QUESTION)
            if dlg.ShowModal() != wx.ID_OK:
                self.wellDisplayChoice.SetSelection(0)
                return
        for platemap in self.plateMaps:
            platemap.SetWellDisplay(sel)

    def OnEnterNumberOfPlates(self, evt=None):
        ''' Handles the entry of a plates to view from a choice box. '''
        try:
            nPlates = int(self.numberOfPlatesTE.GetValue())
        except:
            logging.warn('Invalid # of plates! Please enter a number between 1 and 100')
            return
        if nPlates>100:
            logging.warn('Too many plates! Please enter a number between 1 and 100')
            return
        if nPlates<1:
            logging.warn('You must display at least 1 plate.')
            self.numberOfPlatesTE.SetValue('1')
            nPlates = 1
        # Record the indices of the plates currently selected.
        # Pad the list with sequential plate indices then crop to the new number of plates.
        currentPlates = [plateChoice.GetSelection() for plateChoice in self.plateMapChoices]
        currentPlates = (currentPlates+[(currentPlates[-1]+1+p) % len(self.plateNames) for p in range(nPlates)])[:nPlates]
        # Remove all plateMaps
        self.plateMapSizer.Clear(deleteWindows=True)
        self.plateMaps = []
        self.plateMapChoices = []
        # Restructure the plateMapSizer appropriately
        rows = cols = np.ceil(np.sqrt(nPlates))
        self.plateMapSizer.SetRows(rows)
        self.plateMapSizer.SetCols(cols)
        # Add the plate maps
        for plateIndex in currentPlates:
            self.AddPlateMap(plateIndex)
        self.UpdatePlateMaps()
        self.plateMapSizer.Layout()

    def save_settings(self):
        '''save_settings is called when saving a workspace to file.
        
        returns a dictionary mapping setting names to values encoded as strings
        '''
        settings = {'table' : self.sourceChoice.GetStringSelection(),
                'measurement' : self.measurementsChoice.GetStringSelection(),
                'aggregation' : self.aggregationMethodsChoice.GetStringSelection(),
                'colormap' : self.colorMapsChoice.GetStringSelection(),
                'well display' : self.wellDisplayChoice.GetStringSelection(),
                'number of plates' : self.numberOfPlatesTE.GetValue(),
                }
        for i, choice in enumerate(self.plateMapChoices):
            settings['plate %d'%(i+1)] = choice.GetStringSelection()
        return settings
    
    def load_settings(self, settings):
        '''load_settings is called when loading a workspace from file.
        
        settings - a dictionary mapping setting names to values encoded as
                   strings.
        '''
        if 'table' in settings:
            self.sourceChoice.SetStringSelection(settings['table'])
            self.UpdateMeasurementChoice()
        if 'measurement' in settings:
            self.measurementsChoice.SetStringSelection(settings['measurement'])
            self.OnSelectMeasurement()
        if 'aggregation' in settings:
            self.aggregationMethodsChoice.SetStringSelection(settings['aggregation'])
            self.OnSelectAggregationMethod()
        if 'colormap' in settings:
            self.colorMapsChoice.SetStringSelection(settings['colormap'])
            self.OnSelectColorMap()
        if 'number of plates' in settings:
            self.numberOfPlatesTE.SetValue(settings['number of plates'])
            self.OnEnterNumberOfPlates()
        for s, v in settings.items():
            if s.startswith('plate '):
                self.plateMapChoices[int(s.strip('plate ')) - 1].SetValue(v)
        # set well display last since each step currently causes a redraw and
        # this could take a long time if they are displaying images
        if 'well display' in settings:
            self.wellDisplayChoice.SetStringSelection(settings['well display'])
            self.OnSelectWellDisplay()

def FormatPlateMapData(keys_and_vals, categorical=False):
    '''
    wellsAndVals: a list of lists of plates, wells and values
    returns a 2-tuple containing:
       -an array in the shape of the plate containing the given values with NaNs filling empty slots.  
       -an array in the shape of the plate containing the given keys with (unknownplate, unknownwell) filling empty slots
    '''
    #TODO: well naming format (A##/123..) may have to go in props file
    formats = ['A01', '123', 'Unknown']

    # A01 format also matches A1 below.

    # Make an educated guess at the well naming format
    res = db.execute('SELECT DISTINCT %s FROM %s '%(p.well_id, p.image_table))
    formatA01 = format123 = formatUnknown = 0
    for r in res:
        if re.match(r'^[A-Za-z]\d+$', str(r[0])):
            formatA01 += 1
        elif re.match(r'^\d+$', str(r[0])):
            format123 += 1
        else:
            formatUnknown += 1

    if formatA01 >= format123:
        format = 'A01'
    else:
        format = '123'

    if (formatUnknown > 0) or (formatA01 and format123):
        logging.warn('Could not determine well naming format from the database.  Using "%s", but some wells may be missing.'%(format))

    if p.plate_type == '96': shape = P96
    elif p.plate_type == '384': shape = P384
    elif p.plate_type == '1536': shape = P1536
    elif p.plate_type == '5600': 
        shape = P5600
        data = np.array(keys_and_vals)[:,-1]
        well_keys = np.array(keys_and_vals)[:,:-1]
        assert data.ndim == 1
        if len(data) < 5600: raise Exception(
            '''The measurement you chose to plot was missing for some wells. 
            Because CPA doesn't know the well labelling convention used by this
            microarray, we can't be sure how to plot the data. If you are 
            plotting an object measurement, you probably have no objects in 
            some of your wells.''')
        assert len(data) == 5600
        data = np.array(list(meander(data.reshape(shape)))).reshape(shape)
        well_keys = np.array(list(meander(well_keys.reshape(shape + (2,))))).reshape(shape + (2,))
        return data, well_keys

    data = np.ones(shape) * np.nan
    if categorical:
        data = data.astype('object')
    well_keys = np.array([('UnknownPlate', 'UnknownWell')] * np.prod(shape), dtype=object).reshape(shape + (2,))
    for plate, well, val in keys_and_vals:
        if format == 'A01':
            if re.match('^[a-zA-Z][0-9]+[0-9]?$', well):
                if shape[0] < 26:
                    row = 'abcdefghijklmnopqrstuvwxyz'.index(well[0].lower())
                else:
                    row = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'.index(well[0])
                col = int(well[1:])-1
                if not categorical:
                    data[row,col] = float(val)
                else:
                    data[row,col] = val
                well_keys[row, col] = (plate, well)
        elif format == '123':
            row = (int(well) - 1) / shape[1]
            col = (int(well) - 1) % shape[1]
            if not categorical:
                data[row,col] = float(val)
            else:
                data[row,col] = val
            well_keys[row,col] = well
    return data, well_keys

def meander(a):
    ''' a - 2D array
    returns cells from the array starting at 0,0 and meandering 
    left-to-right on even rows and right-to-left on odd rows'''
    for i, row in enumerate(a):
        if i % 2 ==0:
            for val in row:
                yield val
        else:        
            for val in reversed(row):
                yield val

def get_numeric_columns_from_table(table):
    ''' Fetches names of numeric columns for the given table. '''
    measurements = db.GetColumnNames(table)
    types = db.GetColumnTypes(table)
    return [m for m,t in zip(measurements, types) if t in [float, int, long]]

def get_non_blob_types_from_table(table):
    measurements = db.GetColumnNames(table)
    types = db.GetColumnTypeStrings(table)
    return [m for m,t in zip(measurements, types) if not 'blob' in t.lower()]

if __name__ == "__main__":
    app = wx.PySimpleApp()

    logging.basicConfig()

    # Load a properties file if passed in args
    if len(sys.argv) > 1:
        propsFile = sys.argv[1]
        p.LoadFile(propsFile)
    else:
        if not p.show_load_dialog():
            print 'Plate Viewer requires a properties file.  Exiting.'
            # necessary in case other modal dialogs are up
            wx.GetApp().Exit()
            sys.exit()

#        p.LoadFile('../properties/2009_02_19_MijungKwon_Centrosomes.properties')
#        p.LoadFile('../properties/Gilliland_LeukemiaScreens_Validation.properties')

    pmb = PlateViewer(None)
    pmb.Show()

    app.MainLoop()