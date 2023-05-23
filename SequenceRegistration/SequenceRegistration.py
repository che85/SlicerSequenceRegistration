import os
import unittest
import vtk, qt, ctk, slicer
from slicer.ScriptedLoadableModule import *
import logging

#
# SequenceRegistration
#

class SequenceRegistration(ScriptedLoadableModule):
  """Uses ScriptedLoadableModule base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "Sequence Registration"
    self.parent.categories = ["Sequences"]
    self.parent.dependencies = []
    self.parent.contributors = ["Mohamed Moselhy (Western University), Andras Lasso (PerkLab, Queen's University), and Feng Su (Western University)"]
    self.parent.helpText = """For up-to-date user guides, go to <a href="https://github.com/moselhy/SlicerSequenceRegistration">the official GitHub page</a>
"""
    self.parent.acknowledgementText = """
"""

#
# SequenceRegistrationWidget
#

class SequenceRegistrationWidget(ScriptedLoadableModuleWidget):
  """Uses ScriptedLoadableModuleWidget base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def setup(self):
    ScriptedLoadableModuleWidget.setup(self)

    # Instantiate and connect widgets ...

    self.registrationInProgress = False
    self.logic = SequenceRegistrationLogic()
    self.logic.logCallback = self.addLog

    # Load widget from .ui file (created by Qt Designer).
    # Additional widgets can be instantiated manually and added to self.layout.
    uiWidget = slicer.util.loadUI(self.resourcePath('UI/SequenceRegistration.ui'))
    self.layout.addWidget(uiWidget)
    self.ui = slicer.util.childWidgetVariables(uiWidget)

    self.ui.inputSelector.setMRMLScene(slicer.mrmlScene)
    self.ui.outputVolumesSelector.setMRMLScene(slicer.mrmlScene)
    self.ui.outputTransformSelector.setMRMLScene(slicer.mrmlScene)

    import Elastix
    for preset in self.logic.elastixLogic.getRegistrationPresets():
      self.ui.registrationPresetSelector.addItem(
        f"{preset[Elastix.RegistrationPresets_Modality]} ({preset[Elastix.RegistrationPresets_Content]})")
    self.ui.registrationPresetSelector.addItem("*NEW*")
    self.newPresetIndex = self.ui.registrationPresetSelector.count - 1

    self.ui.customElastixBinDirSelector.setCurrentPath(self.logic.elastixLogic.getCustomElastixBinDir())

    # connections
    self.ui.applyButton.connect('clicked(bool)', self.onApplyButton)
    self.ui.inputSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onInputSelect)
    self.ui.outputVolumesSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onSelect)
    self.ui.outputTransformSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onSelect)
    self.ui.sequenceFixedItemIndexWidget.connect('valueChanged(double)', self.setSequenceItemIndex)
    self.ui.sequenceStartItemIndexWidget.connect('valueChanged(double)', self.setSequenceItemIndex)
    self.ui.sequenceEndItemIndexWidget.connect('valueChanged(double)', self.setSequenceItemIndex)
    self.ui.showTemporaryFilesFolderButton.connect('clicked(bool)', self.onShowTemporaryFilesFolder)
    self.ui.showRegistrationParametersDatabaseFolderButton.connect('clicked(bool)', self.onShowRegistrationParametersDatabaseFolder)
    # Immediately update deleteTemporaryFiles and show detailed logs in the logic to make it possible to decide to
    # update these variables while the registration is running
    self.ui.keepTemporaryFilesCheckBox.connect("toggled(bool)", self.onKeepTemporaryFilesToggled)
    self.ui.showDetailedLogDuringExecutionCheckBox.connect("toggled(bool)", self.onShowLogToggled)
    # Check if user selects to create a new preset
    self.ui.registrationPresetSelector.connect("activated(int)", self.onCreatePresetPressed)

    # Add vertical spacer
    self.layout.addStretch(1)

    # Variable initializations
    self.newParameterButtons = []

    # Refresh Apply button state
    self.onInputSelect()

  def setSequenceItemIndex(self, index):
    sequenceBrowserNode = self.logic.findBrowserForSequence(self.ui.inputSelector.currentNode())
    sequenceBrowserNode.SetSelectedItemNumber(int(index))

  def onCreatePresetPressed(self):
    if self.ui.registrationPresetSelector.currentIndex != self.newPresetIndex:
      return

    self.newPresetBox = qt.QDialog()
    self.customPresetLayout = qt.QVBoxLayout()

    self.addParameterFile()

    addPresetButton = qt.QPushButton("Add more presets...")
    addPresetButton.connect("clicked(bool)", self.addParameterFile)
    self.customPresetLayout.addWidget(addPresetButton)
    self.newPresetBox.setLayout(self.customPresetLayout)


    # Add fields to specify descriptions, etc... for that preset (to be included in the XML file)

    groupBox = qt.QGroupBox()
    formLayout = qt.QFormLayout()

    self.contentBox = qt.QLineEdit()
    formLayout.addRow("Content: ", self.contentBox)
    self.descriptionBox = qt.QLineEdit()
    formLayout.addRow("Description: ", self.descriptionBox)
    self.idBox = qt.QLineEdit()
    formLayout.addRow("Id: ", self.idBox)
    self.modalityBox = qt.QLineEdit()
    formLayout.addRow("Modality: ", self.modalityBox)
    self.publicationsBox = qt.QPlainTextEdit()
    formLayout.addRow("Publications: ", self.publicationsBox)

    groupBox.setLayout(formLayout)
    self.customPresetLayout.addWidget(groupBox)

    # Add Ok/Cancel buttons and connect them to the main dialog
    buttonBox = qt.QDialogButtonBox()
    buttonBox.setStandardButtons(qt.QDialogButtonBox.Ok | qt.QDialogButtonBox.Cancel)
    buttonBox.setCenterButtons(True)
    buttonBox.connect("accepted()", self.newPresetBox.accept)
    buttonBox.connect("rejected()", self.newPresetBox.reject)

    self.customPresetLayout.addWidget(buttonBox)

    response = self.newPresetBox.exec_()

    if response:
      self.createPreset()

  def createPreset(self):
    filenames = []
    # Get all the filenames that the user included
    for includeButton in self.newPresetBox.findChildren(qt.QPushButton):
      if includeButton.isChecked():
        row = self.newParameterButtons[self.getRowNumber(includeButton)]
        filepath = os.path.realpath(row[0].text)
        if os.path.exists(filepath):
          filenames.append(filepath)
        else:
          logging.error("File \"%s\" was not included, it was not found in %s" % (os.path.basename(filepath), os.path.dirname(filepath)))

    if len(filenames) > 0:
      from shutil import copyfile
      import xml.etree.ElementTree as ET
      databaseDir = self.logic.elastixLogic.registrationParameterFilesDir
      presetDatabase = os.path.join(databaseDir, 'ElastixParameterSetDatabase.xml')
      xml = ET.parse(presetDatabase)
      root = xml.getroot()
      attributes = {}
      attributes['content'] = self.contentBox.text
      attributes['description'] = self.descriptionBox.text
      attributes['id'] = self.idBox.text
      attributes['modality'] = self.modalityBox.text
      attributes['publications'] = self.publicationsBox.plainText

      presetElement = ET.SubElement(root, "ParameterSet", attributes)
      parFilesElement = ET.SubElement(presetElement, "ParameterFiles")

      # Copy parameter files to database directory
      for file in filenames:
        filename = os.path.basename(file)
        newFilePath = os.path.join(databaseDir, filename)
        if os.path.exists(newFilePath) and not self.overwriteParFile(filename):
          continue
        copyfile(file, newFilePath)
        ET.SubElement(parFilesElement, "File", {"Name" : filename})

      xml.write(presetDatabase)

    # Destroy old dialog box
    self.newPresetBox.delete()
    self.newParameterButtons = []

    # Refresh list and select new preset
    self.selectNewPreset()

  def selectNewPreset(self):
    import Elastix
    self.logic = SequenceRegistrationLogic()
    allPresets = self.logic.elastixLogic.getRegistrationPresets()
    preset = allPresets[len(allPresets) - 1]
    self.ui.registrationPresetSelector.insertItem(self.newPresetIndex, "{0} ({1})".format(preset[Elastix.RegistrationPresets_Modality], preset[Elastix.RegistrationPresets_Content]))
    self.ui.registrationPresetSelector.currentIndex = self.newPresetIndex
    self.newPresetIndex += 1

  def overwriteParFile(self, filename):
    d = qt.QDialog()
    resp = qt.QMessageBox.warning(d, "Overwrite File?", "File \"%s\" already exists, do you want to overwrite it? (Clicking Discard would exclude the file from the preset)" % filename, qt.QMessageBox.Save | qt.QMessageBox.Discard, qt.QMessageBox.Save)
    return resp == qt.QMessageBox.Save

  def addParameterFile(self):
    lastSelectorIndex = self.customPresetLayout.count() - 3
    parameterFilePathButton = qt.QPushButton("Select a file")
    parameterFileToggleButton = qt.QPushButton("Include")
    parameterFileToggleButton.setCheckable(True)

    rowLayout = qt.QHBoxLayout()
    rowLayout.addWidget(parameterFilePathButton)
    rowLayout.addWidget(parameterFileToggleButton)

    self.newParameterButtons.append((parameterFilePathButton, parameterFileToggleButton))
    self.customPresetLayout.insertLayout(lastSelectorIndex, rowLayout)

    parameterFilePathButton.connect("clicked(bool)", lambda: self.selectParameterFile(parameterFilePathButton))

  def selectParameterFile(self, sender):
    sender.setText(qt.QFileDialog.getOpenFileName())
    row = self.newParameterButtons[self.getRowNumber(sender)]
    row[1].setChecked(True)

  def getRowNumber(self, sender):
    for row in self.newParameterButtons:
      if sender in row:
        return self.newParameterButtons.index(row)

  def cleanup(self):
    pass

  def onInputSelect(self):
    if not self.ui.inputSelector.currentNode():
      numberOfDataNodes = 0
    else:
      numberOfDataNodes = self.ui.inputSelector.currentNode().GetNumberOfDataNodes()

    for sequenceItemSelectorWidget in [self.ui.sequenceFixedItemIndexWidget, self.ui.sequenceStartItemIndexWidget, self.ui.sequenceEndItemIndexWidget]:
      if numberOfDataNodes < 1:
        sequenceItemSelectorWidget.maximum = 0
        sequenceItemSelectorWidget.enabled = False
      else:
        sequenceItemSelectorWidget.maximum = numberOfDataNodes-1
        sequenceItemSelectorWidget.enabled = True

    self.ui.sequenceFixedItemIndexWidget.value =  int(self.ui.sequenceStartItemIndexWidget.maximum / 2)
    self.ui.sequenceStartItemIndexWidget.value =  0
    self.ui.sequenceEndItemIndexWidget.value = self.ui.sequenceEndItemIndexWidget.maximum

    self.onSelect()

  def onSelect(self):

    self.ui.applyButton.enabled = self.ui.inputSelector.currentNode() and (self.ui.outputVolumesSelector.currentNode() or self.ui.outputTransformSelector.currentNode())

    if not self.registrationInProgress:
      self.ui.applyButton.text = "Register"
      return
    self.updateBrowsers()

  def onApplyButton(self):

    if self.registrationInProgress:
      self.registrationInProgress = False
      self.logic.setAbortRequested(True)
      self.ui.applyButton.text = "Cancelling..."
      self.ui.applyButton.enabled = False
      return

    self.registrationInProgress = True
    self.ui.applyButton.text = "Cancel"
    self.ui.statusLabel.plainText = ''
    slicer.app.setOverrideCursor(qt.Qt.WaitCursor)
    try:
      computeMovingToFixedTransform = (self.ui.transformDirectionSelector.currentIndex == 0)
      fixedFrameIndex = int(self.ui.sequenceFixedItemIndexWidget.value)
      startFrameIndex = int(self.ui.sequenceStartItemIndexWidget.value)
      endFrameIndex = int(self.ui.sequenceEndItemIndexWidget.value)
      self.logic.elastixLogic.setCustomElastixBinDir(self.ui.customElastixBinDirSelector.currentPath)
      self.logic.logStandardOutput = self.ui.showDetailedLogDuringExecutionCheckBox.checked
      self.logic.registerVolumeSequence(self.ui.inputSelector.currentNode(),
        self.ui.outputVolumesSelector.currentNode(), self.ui.outputTransformSelector.currentNode(),
        fixedFrameIndex, self.ui.registrationPresetSelector.currentIndex, computeMovingToFixedTransform,
        startFrameIndex, endFrameIndex)
    except Exception as e:
      print(e)
      self.addLog("Error: {0}".format(str(e)))
      import traceback
      traceback.print_exc()
    finally:
      slicer.app.restoreOverrideCursor()
      self.registrationInProgress = False
      self.onSelect() # restores default Apply button state

  def addLog(self, text):
    """Append text to log window
    """
    self.ui.statusLabel.appendPlainText(text)
    slicer.app.processEvents()  # force update

  def onShowTemporaryFilesFolder(self):
    qt.QDesktopServices().openUrl(qt.QUrl("file:///" + self.logic.elastixLogic.getTempDirectoryBase(), qt.QUrl.TolerantMode))

  def onKeepTemporaryFilesToggled(self, toggle):
    self.logic.elastixLogic.deleteTemporaryFiles = not toggle

  def onShowRegistrationParametersDatabaseFolder(self):
    qt.QDesktopServices().openUrl(qt.QUrl("file:///" + self.logic.elastixLogic.registrationParameterFilesDir, qt.QUrl.TolerantMode))

  def onShowLogToggled(self, toggle):
    self.logic.elastixLogic.logStandardOutput = toggle


#
# SequenceRegistrationLogic
#

class SequenceRegistrationLogic(ScriptedLoadableModuleLogic):
  """This class should implement all the actual
  computation done by your module.  The interface
  should be such that other python code can import
  this class and make use of the functionality without
  requiring an instance of the Widget.
  Uses ScriptedLoadableModuleLogic base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def __init__(self):
    ScriptedLoadableModuleLogic.__init__(self)
    self.logStandardOutput = False
    self.logCallback = None

    import Elastix
    self.elastixLogic = Elastix.ElastixLogic()

  def setAbortRequested(self, abortRequested):
    self.elastixLogic.abortRequested = abortRequested

  def findBrowserForSequence(self, sequenceNode):
    browserNodes = slicer.util.getNodesByClass("vtkMRMLSequenceBrowserNode")
    for browserNode in browserNodes:
      if browserNode.IsSynchronizedSequenceNode(sequenceNode, True):
        return browserNode
    return None

  def registerVolumeSequence(self, inputVolSeq, outputVolSeq, outputTransformSeq, fixedVolumeItemNumber, presetIndex,
                             computeMovingToFixedTransform=True, startFrameIndex=None, endFrameIndex=None):
    """
    computeMovingToFixedTransform: if True then moving->fixed else fixed->moving transforms are computed
    """
    self.elastixLogic.logStandardOutput = self.logStandardOutput
    self.elastixLogic.logCallback = self.logCallback
    self.abortRequested = False

    import Elastix
    parameterFilenames = self.elastixLogic.getRegistrationPresets()[presetIndex][Elastix.RegistrationPresets_ParameterFilenames]

    fixedSeqBrowser = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSequenceBrowserNode")
    fixedSeqBrowser.SetAndObserveMasterSequenceNodeID(inputVolSeq.GetID())
    fixedSeqBrowser.SetSelectedItemNumber(fixedVolumeItemNumber)
    if slicer.app.majorVersion*100+slicer.app.minorVersion < 411:
      sequencesModule = slicer.modules.sequencebrowser
    else:
      sequencesModule = slicer.modules.sequences
    sequencesModule.logic().UpdateAllProxyNodes()
    slicer.app.processEvents()
    fixedVolume = fixedSeqBrowser.GetProxyNode(inputVolSeq)

    movingSeqBrowser = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSequenceBrowserNode")
    movingSeqBrowser.SetAndObserveMasterSequenceNodeID(inputVolSeq.GetID())

    # Initialize output sequences
    for seq in [outputVolSeq, outputTransformSeq]:
      if seq:
        seq.RemoveAllDataNodes()
        seq.SetIndexType(inputVolSeq.GetIndexType())
        seq.SetIndexName(inputVolSeq.GetIndexName())
        seq.SetIndexUnit(inputVolSeq.GetIndexUnit())

    outputVol = slicer.mrmlScene.AddNewNodeByClass(fixedVolume.GetClassName())

    # Only request output transform if it is needed, to save some time on computing it
    if outputTransformSeq:
      outputTransform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTransformNode")
    else:
      outputTransform = None

    try:
      numberOfDataNodes = inputVolSeq.GetNumberOfDataNodes()
      if startFrameIndex is None:
        startFrameIndex = 0
      if endFrameIndex is None:
        endFrameIndex = numberOfDataNodes-1
      movingVolIndices = list(range(startFrameIndex, endFrameIndex+1))
      for movingVolumeItemNumber in movingVolIndices:
        if movingVolumeItemNumber>movingVolIndices[0]:
          self.elastixLogic.addLog("---------------------")
        self.elastixLogic.addLog("Registering item {0} of {1}".format(movingVolumeItemNumber-movingVolIndices[0]+1, len(movingVolIndices)))
        movingSeqBrowser.SetSelectedItemNumber(movingVolumeItemNumber)
        sequencesModule.logic().UpdateProxyNodesFromSequences(movingSeqBrowser)
        movingVolume = movingSeqBrowser.GetProxyNode(inputVolSeq)

        if movingVolumeItemNumber != fixedVolumeItemNumber:
          self.elastixLogic.registerVolumes(
            fixedVolume, movingVolume,
            outputVolumeNode = outputVol,
            parameterFilenames = parameterFilenames,
            outputTransformNode = outputTransform
            )

          if outputVolSeq:
            outputVolSeq.SetDataNodeAtValue(outputVol, inputVolSeq.GetNthIndexValue(movingVolumeItemNumber))
          if outputTransformSeq:
            if not computeMovingToFixedTransform:
              outputTransform.Inverse()
            outputTransformSeq.SetDataNodeAtValue(outputTransform, inputVolSeq.GetNthIndexValue(movingVolumeItemNumber))
        else:
          self.elastixLogic.addLog("Same as fixed volume.")
          if outputVolSeq:
            outputVolSeq.SetDataNodeAtValue(fixedVolume, inputVolSeq.GetNthIndexValue(movingVolumeItemNumber))
            outputVolSeq.GetDataNodeAtValue(inputVolSeq.GetNthIndexValue(movingVolumeItemNumber)).SetName(slicer.mrmlScene.GetUniqueNameByString("Volume"))

          if outputTransformSeq:
            # Set identity as transform (vtkTransform is initialized to identity transform by default)
            outputTransform.SetAndObserveTransformToParent(vtk.vtkTransform())
            outputTransformSeq.SetDataNodeAtValue(outputTransform, inputVolSeq.GetNthIndexValue(movingVolumeItemNumber))

      if outputVolSeq:
        # Make scalar type of the fixed volume in the output sequence to the other volumes
        outputFixedVol = outputVolSeq.GetDataNodeAtValue(inputVolSeq.GetNthIndexValue(fixedVolumeItemNumber))
        imageCast = vtk.vtkImageCast()
        ijkToRasMatrix = vtk.vtkMatrix4x4()
        imageCast.SetInputData(outputFixedVol.GetImageData())
        imageCast.SetOutputScalarTypeToShort()
        imageCast.Update()
        outputFixedVol.SetAndObserveImageData(imageCast.GetOutput())
        # Make origin and spacing match exactly other volumes
        movingVolIndices.remove(fixedVolumeItemNumber)
        if len(movingVolIndices) >= 1:
          matchedVolumeIndex = movingVolIndices[0]
          matchedVolume = outputVolSeq.GetDataNodeAtValue(inputVolSeq.GetNthIndexValue(matchedVolumeIndex))
          outputFixedVol.SetOrigin(matchedVolume.GetOrigin())
          outputFixedVol.SetSpacing(matchedVolume.GetSpacing())

    finally:

      # Temporary result nodes
      slicer.mrmlScene.RemoveNode(outputVol)
      if outputTransformSeq:
        slicer.mrmlScene.RemoveNode(outputTransform)
      # Temporary input browser nodes
      slicer.mrmlScene.RemoveNode(fixedSeqBrowser)
      slicer.mrmlScene.RemoveNode(movingSeqBrowser)
      # Temporary input volume proxy nodes
      slicer.mrmlScene.RemoveNode(fixedVolume)
      slicer.mrmlScene.RemoveNode(movingVolume)

      # Move output sequences in the same browser node as the input volume sequence and rename their proxy nodes
      outputBrowserNode = self.findBrowserForSequence(inputVolSeq)

      if outputBrowserNode:
        if outputVolSeq and not self.findBrowserForSequence(outputVolSeq):
          outputBrowserNode.AddSynchronizedSequenceNodeID(outputVolSeq.GetID())
          outputBrowserNode.SetOverwriteProxyName(outputVolSeq, True)
        if outputTransformSeq and not self.findBrowserForSequence(outputTransformSeq):
          outputBrowserNode.AddSynchronizedSequenceNodeID(outputTransformSeq.GetID())
          outputBrowserNode.SetOverwriteProxyName(outputTransformSeq, True)


class SequenceRegistrationTest(ScriptedLoadableModuleTest):
  """
  This is the test case for your scripted module.
  Uses ScriptedLoadableModuleTest base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def setUp(self):
    """ Do whatever is needed to reset the state - typically a scene clear will be enough.
    """
    slicer.mrmlScene.Clear(0)

  def runTest(self):
    """Run as few or as many tests as needed here.
    """
    self.setUp()
    self.test_SequenceRegistration()

  def test_SequenceRegistration(self):
    """ Ideally you should have several levels of tests.  At the lowest level
    tests should exercise the functionality of the logic with different inputs
    (both valid and invalid).  At higher levels your tests should emulate the
    way the user would interact with your code and confirm that it still works
    the way you intended.
    One of the most important features of the tests is that it should alert other
    developers when their changes will have an impact on the behavior of your
    module.  For example, if a developer removes a feature that you depend on,
    your test should break so they know that the feature is needed.
    """

    self.delayDisplay("Starting the test")
    slicer.app.setOverrideCursor(qt.Qt.WaitCursor)
    #
    # first, get some data
    #

    import SampleData
    sampleDataLogic = SampleData.SampleDataLogic()
    inputVolSeqBrowser = sampleDataLogic.downloadSample("CTPCardio")
    inputVolSeq = inputVolSeqBrowser.GetMasterSequenceNode()

    outputVolSeq = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSequenceNode", "OutVolSeq")
    outputTransformSeq = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSequenceNode", "OutTransformSeq")

    for i in range(inputVolSeq.GetNumberOfDataNodes())[3:]:
      inputVolSeq.RemoveDataNodeAtValue(str(i))

    self.delayDisplay('Starting registration...')

    import Elastix
    logic = SequenceRegistrationLogic()
    logic.registerVolumeSequence(inputVolSeq, outputVolSeq, outputTransformSeq, 1, 0)

    slicer.app.restoreOverrideCursor()
    self.delayDisplay('Test passed!')
