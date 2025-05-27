import collections
import json
import os
import re
import sys
import time
#
import idc
import idaapi
import idautils
#
from idaclu import ida_shims
from idaclu.qt_shims import (
    QCoreApplication,
    QCursor,
    Qt,
    QtCore,
    QFrame,
    QIcon,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QSize,
    QSizePolicy,
    QSpacerItem,
    QStandardItem,
    QStandardItemModel,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget
)
from idaclu import ida_utils
from idaclu import plg_utils
from idaclu.ui_idaclu import Ui_PluginDialog
from idaclu.qt_utils import i18n
from idaclu.qt_widgets import FrameLayout
from idaclu.models import ResultModel, ResultNode
from idaclu.assets import resource

# new backward-incompatible modules
try:
    import ida_dirtree
except ImportError:
    pass


class InstrumentedCallback:
    """A wrapper class to count accesses to a callback."""

    def __init__(self, func, pass_count=0):
        self.func = func
        self.pass_count = pass_count
        self.call_count = 0

    def reset(self):
        self.call_count = 0

    def __call__(self, *args):
        self.call_count += 1

        if self.pass_count == 0:
            return self.func()
        else:
            return self.func(self.call_count, self.pass_count)

    def get_call_count(self):
        return self.call_count


class AppendTextEditDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        return editor

    def setEditorData(self, editor, index):
        current_text = index.data()
        editor.setText(current_text)

    def setModelData(self, editor, model, index):
        current_text = index.data()
        new_text = editor.text()
        appended_text = "{}".format(new_text)
        model.setData(index, appended_text)
        func_addr = ida_shims.get_name_ea(0, current_text)
        ida_shims.set_name(func_addr, new_text, idaapi.SN_NOWARN)


class IdaCluDialog(QWidget):
    def __init__(self, env_desc):
        super(IdaCluDialog, self).__init__()
        self.env_desc = env_desc
        self.ui = Ui_PluginDialog(env_desc)
        self.ui.setupUi(self)

        self.ui.rvTable.setItemDelegate(AppendTextEditDelegate())

        self.is_sidebar_on_left = True
        self.is_filters_shown = True
        self.option_sender = None
        self.is_mode_recursion = False
        # values to initialize the corresponding filter

        self.clu_data = {}
        if self.env_desc.feat_folders:
            folders = ida_utils.get_func_dirs('/')
            self.clu_data['dirs'] = ida_utils.get_dir_funcs(folders)

        self.sel_dirs = []
        self.sel_prfx = []
        self.sel_colr = []

        sp_path = self.get_splg_root(self.env_desc.plg_src, 'idaclu')
        for frame in self.get_sp_controls(sp_path):
            self.ui.ScriptsContentsLayout.addWidget(frame)

        self.ui.wColorTool.setClickHandler(self.changeFuncColor)

        self.initFoldersFilter()
        self.initPrefixFilter()
        self.initColorFilter()
        self.bindUiElems()

    def toggleRecursion(self):
        self.is_mode_recursion = not self.is_mode_recursion

    def bindUiElems(self):
        self.bindClicks()
        self.ui.rvTable.doubleClicked.connect(self.treeDoubleClick)
        self.ui.rvTable.customContextMenuRequested.connect(self.showContextMenu)

    def bindClicks(self):
        feat_folders = self.env_desc.feat_folders
        bind_data = [
            (self.ui.ScriptsHeader, self.swapPosition, True),
            (self.ui.FiltersHeader, self.showFilters, True)
        ]
        for (elem, meth, cond) in bind_data:
            if cond:
                elem.clicked.connect(meth)
        self.ui.wLabelTool.setModeHandler(self.toggleRecursion)
        self.ui.wLabelTool.setSetHandler(self.addLabel)
        self.ui.wLabelTool.setClsHandler(self.clsLabel)

    def getFuncPrefs(self, is_dummy=False):
        pfx_afacts = ['%', '_']
        prefs = collections.defaultdict(int)
        for func_addr in idautils.Functions():
            func_name = ida_shims.get_func_name(func_addr)
            func_name = func_name.lstrip('_')
            if any(pa in func_name for pa in pfx_afacts):
                func_prefs = ida_utils.get_func_prefs(func_name, is_dummy)
                for pfx in func_prefs:
                    prefs[pfx] += 1
        return list(prefs.items())

    def getFuncColors(self):
        color_map = collections.defaultdict(int)
        for func_addr in idautils.Functions():
            func_colr = ida_shims.get_color(func_addr, idc.CIC_FUNC)
            color_map[func_colr] += 1

        colors = []
        for k,v in color_map.items():
            rgb = plg_utils.RgbColor(k)
            rgb.invert_color()
            colors.append((rgb.get_to_name(), v, rgb.get_to_tuple()))

        return colors

    def viewSelChanged(self):
        self.ui.wLabelTool.setEnabled(True)
        self.ui.wColorTool.setEnabled(True)

    def initPrefixFilter(self):
        prefixes = self.getFuncPrefs(is_dummy=True)
        self.ui.wPrefixFilter.addItems(prefixes, True)
        self.ui.wPrefixFilter.setText("")

    def initColorFilter(self):
        colors = self.getFuncColors()
        self.ui.wColorFilter.addItems(colors)
        self.ui.wColorFilter.setText("")

    def initFoldersFilter(self):
        if self.env_desc.feat_folders:
            folders = ida_utils.get_dir_metrics('/')
            self.ui.wFolderFilter.addItems(folders, True)
            self.ui.wFolderFilter.setText("")
        else:
            self.ui.wFolderFilter.removeSelf()
            self.ui.FolderFilterLayout.setParent(None)
            layout = self.ui.vlFiltersGroup
            item = layout.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    widget.deleteLater()
                del item

    def sample_generator(self):
        if False:
            yield

    def has_parent_widget(self, sender_button, dropdown_class):
        parent_widget = sender_button.parent()
        for i in range(parent_widget.layout().count()):
            sub_item = parent_widget.layout().itemAt(i)
            sub_widget = sub_item.widget()
            if sub_widget and (isinstance(sub_widget, dropdown_class)):
                return True
        return False

    def get_plugin_data(self):
        self.ui.rvTable.setModelProxy(ResultModel(self.ui.rvTable.heads, [], self.env_desc))

        try:
            sender_button = self.sender()
            self.ui.rvTable.rec_indx.clear()

            full_spec_name = sender_button.objectName()
            elem, cat, plg = full_spec_name.split('#')

            root_folder = self.env_desc.plg_src
            module = None
            with plg_utils.PluginPath(os.path.join(root_folder, 'idaclu', 'plugins', cat)):
                module = __import__(plg)
                del sys.modules[plg]

            script_name = getattr(module, 'SCRIPT_NAME')
            script_type = getattr(module, 'SCRIPT_TYPE', 'custom')
            script_view = getattr(module, 'SCRIPT_VIEW', 'table')
            script_args = getattr(module, 'SCRIPT_ARGS', [])

            if not script_type in ['func', 'custom']:
                ida_shims.msg('ERROR: Unknown plugin type')
                return

            directory = os.path.dirname(self.env_desc.idb_path)
            json_filename = "{}_idaclu_{}.json".format(self.env_desc.ida_module, script_name.lower().replace(' ', '_'))
            cs_cache_file = os.path.join(directory, json_filename)

            cs_data = None
            is_pre_filter = script_type == 'func'
            func_filter = self.updatePbFunc if is_pre_filter else self.updatePb

            if self.ui.ConfigTool.is_save and os.path.isfile(cs_cache_file):
                with open(cs_cache_file, "r") as json_file:
                    cs_data = json.load(json_file)
                self.ui.wProgressBar.updateProgress(50, "Phase: loading")
            else:
                if os.path.isfile(cs_cache_file):
                    os.remove(cs_cache_file)

                plug_params = {}
                if self.option_sender != None:
                    widget = self.ui.ScriptsArea.findChild(QPushButton, self.option_sender)
                    parent_layout = widget.parent().layout()

                    if self.option_sender == full_spec_name:
                        for i in range(parent_layout.count()):
                            sub_item = parent_layout.itemAt(i)
                            if sub_item:
                                sub_widget = sub_item.widget()
                                if sub_widget and type(sub_widget) == QFrame:
                                    param_name = sub_widget.objectName().replace("{}__".format(full_spec_name), "")
                                    states = []
                                    for i in range(sub_widget.layout().count()):
                                        widget = sub_widget.layout().itemAt(i).widget()
                                        if isinstance(widget, QLineEdit):
                                            states.append(widget.text())  # .toPlainText()
                                    plug_params[param_name] = states
                                if sub_widget and type(sub_widget) == QListView:
                                    param_name = sub_widget.objectName().replace("{}__".format(full_spec_name), "")
                                    states = []
                                    for row in range(sub_widget.model().rowCount()):
                                        item = sub_widget.model().item(row)
                                        text = item.text()
                                        checked = item.checkState() == Qt.Checked
                                        states.append((text, checked))
                                    plug_params[param_name] = states

                    for i in range(parent_layout.count()):
                        sub_item = parent_layout.itemAt(i)
                        if sub_item:
                            # if isinstance(sub_item, QSpacerItem):
                            #     parent_layout.removeItem(sub_item)
                            #     continue
                            sub_widget = sub_item.widget()
                            if sub_widget and type(sub_widget) in [QFrame, QListView]:
                                parent_layout.removeWidget(sub_widget)
                                sub_widget.setParent(None)

                    self.option_sender = None

                elif self.option_sender == None and len(script_args) > 0:
                    parent_widget = sender_button.parent()
                    if parent_widget:
                        for i, (ctrl_name, var_name, ctrl_ctx) in enumerate(script_args):
                            if ctrl_name == "textedit":
                                if not self.has_parent_widget(sender_button, QFrame):
                                    content_widget = QFrame()
                                    vbox = QVBoxLayout(content_widget)
                                    parent_widget.layout().addWidget(content_widget)
                                    content_widget.setMaximumSize(QSize(16777215, 60))
                                    content_widget.setObjectName("{}__{}".format(full_spec_name, var_name))
                                    for text in ctrl_ctx:
                                        text_edit = QLineEdit()
                                        text_edit.setPlaceholderText(text)
                                        vbox.addWidget(text_edit)
                            if ctrl_name == "checkbox":
                                if not self.has_parent_widget(sender_button, QListView):
                                    list_view = QListView()
                                    parent_widget.layout().addWidget(list_view)
                                    parent_widget.setMaximumSize(QSize(16777215, 160))
                                    model = QStandardItemModel()
                                    list_view.setModel(model)
                                    list_view.setObjectName("{}__{}".format(full_spec_name, var_name))

                                    for text in ctrl_ctx:
                                        item = QStandardItem(text)
                                        item.setCheckable(True)
                                        item.setCheckState(False)  # Unchecked
                                        model.appendRow(item)

                        # spacer = QSpacerItem(20, 30, QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)
                        # parent_widget.layout().addStretch(1)
                        self.option_sender = full_spec_name
                        return

                get_cs_data = getattr(module, 'get_data')

                gen = InstrumentedCallback(self.sample_generator)
                get_cs_data(gen, self.env_desc, plug_params)
                phase_count = gen.get_call_count()
                gen = InstrumentedCallback(func_filter, phase_count)
                cs_data = get_cs_data(gen, self.env_desc, plug_params)

                if self.ui.ConfigTool.is_save:
                    with open(cs_cache_file, "w") as json_file:
                        json.dump(cs_data, json_file, indent=4)

            self.items = []

            cp_data = collections.defaultdict(list)
            cs_func_count = sum(len(band_fns) for band_fns in cs_data.values())
            cs_func_idx = 0

            if (self.ui.ConfigTool.is_save or is_pre_filter == False):
                self.sel_dirs = self.ui.wFolderFilter.getData()
                self.sel_prfx = self.ui.wPrefixFilter.getData()
                self.sel_colr = self.ui.wColorFilter.getData()

            # Iterating over "rubber-banded hooks" where:
            #  - the "band" - is function cluster
            #  - the "hook" - is function address (with optional comment)
            # The aim to augment "hooks" with useful for analysis data
            # to be presented in main tree-table view of the plugin.
            for band_nam in cs_data:
                for hook_val in cs_data[band_nam]:
                    func_addr, func_cmnt = None, None
                    if isinstance(hook_val, int):
                        func_addr, func_cmnt = hook_val, ""
                    elif self.env_desc.ver_py == 2 and isinstance(hook_val, long):
                        func_addr, func_cmnt = int(hook_val), ""
                    elif isinstance(hook_val, tuple) or isinstance(hook_val, list):
                        func_addr = int(hook_val[0])  # long in IDA v6.x;
                        func_cmnt = str(hook_val[1])  # just in case

                    if (self.ui.ConfigTool.is_save or is_pre_filter == False) and self.isFuncRelevant(func_addr) == False:
                        continue

                    # Getting function info from function "hook".
                    func_inst = idaapi.get_func(func_addr)
                    func_name = ida_shims.get_func_name(func_addr)
                    func_colr = plg_utils.RgbColor(ida_shims.get_color(func_addr, idc.CIC_FUNC))
                    func_colr.invert_color()
                    func_path = None
                    func_node, func_edge = ida_utils.get_nodes_edges(func_addr)

                    # Storing function data.
                    func_desc = collections.OrderedDict()
                    func_desc['func_name'] = func_name

                    if self.env_desc.feat_folders:
                        dir_info = self.clu_data['dirs']
                        func_path = dir_info[func_addr] if func_addr in dir_info else '/'
                        func_desc['func_path'] = func_path

                    func_desc['func_addr'] = hex(func_addr)
                    func_desc['func_size'] = ida_shims.calc_func_size(func_inst)
                    func_desc['func_chnk'] = len(list(idautils.Chunks(func_addr)))
                    func_desc['func_node'] = func_node  # graph node count
                    func_desc['func_edge'] = func_edge  # graph edge count
                    func_desc['func_cmnt'] = func_cmnt
                    func_desc['func_colr'] = func_colr.get_to_str()

                    cp_data[band_nam].append(func_desc)
                    cs_func_idx += 1
                    # Augmenting function data is represented as 15% of progress.
                    cs_prog = plg_utils.get_prog_val(50, 15, cs_func_idx, cs_func_count)
                    self.ui.wProgressBar.updateProgress(cs_prog, "Phase: augmenting")

            # Constructing list of node trees.
            # The list contains only parent nodes, that internally have references to child nodes.
            cs_func_idx = 0
            for band_idx, (band_nam, func_dss) in enumerate(cp_data.items()):
                self.items.append(ResultNode("{} ({})".format(band_nam, len(func_dss))))
                for func_idx, func_dsc in enumerate(func_dss):
                    self.items[-1].addChild(ResultNode(list(func_dsc.values())))
                    cs_func_idx += 1
                    finished = plg_utils.get_prog_val(65, 30, cs_func_idx, cs_func_count)
                    self.ui.rvTable.rec_indx[int(func_dsc['func_addr'], 16)].append((band_idx, func_idx))
                    self.ui.wProgressBar.updateProgress(finished, "Phase: indexing")

            self.ui.rvTable.setModelProxy(ResultModel(self.ui.rvTable.heads, self.items, self.env_desc))
            self.ui.wProgressBar.updateProgress(100, "Phase: completing")
            self.prepareView()
        except plg_utils.UserCancelledError:
            return

    def prepareView(self):
        view = self.ui.rvTable
        rvTableSelModel = view.selectionModel()
        tree_header = view.header()

        view.setColumnHidden(self.ui.rvTable.heads.index('Color'), True)
        rvTableSelModel.selectionChanged.connect(self.viewSelChanged)
        tree_header.resizeSection(0, 240)
        tree_header.resizeSection(1, 96)
        tree_header.resizeSection(2, 96)
        tree_header.resizeSection(3, 96)

    def updatePb(self, curr_idx, total_count):
        finished = int(70 * (curr_idx / float(total_count)))
        finished_msg = "Phase: searching (steps {}/{})".format(curr_idx, total_count)
        try:
            self.ui.wProgressBar.updateProgress(finished, finished_msg)
        except plg_utils.UserCancelledError:
            raise plg_utils.UserCancelledError

    def updatePbFunc(self, pass_index=1, pass_count=1):
        self.sel_dirs = self.ui.wFolderFilter.getData()
        self.sel_prfx = self.ui.wPrefixFilter.getData()
        self.sel_colr = self.ui.wColorFilter.getData()

        func_desc = list(idautils.Functions())
        func_count = len(func_desc)
        for func_index, func_addr in enumerate(func_desc):

            if not self.isFuncRelevant(func_addr):
                continue

            progress = None
            finished = None

            if pass_count == 1:
                index = func_index + 1
                count = func_count
                name = "funcs"

                progress = func_index / float(func_count)
                finished = int(50 * progress)
            else:
                index = pass_index
                count = pass_count
                name = "steps"

                pass_contrib_one = 50 / pass_count
                pass_contrib_sum = pass_contrib_one * (pass_index - 1)
                progress = func_index / float(func_count)
                finished =int(pass_contrib_sum + pass_contrib_one * progress)

            finished_msg = "Phase: searching ({} {}/{})".format(name, index, count)
            try:
                self.ui.wProgressBar.updateProgress(finished, finished_msg)
            except plg_utils.UserCancelledError:
                raise plg_utils.UserCancelledError

            yield func_addr

    def isFuncRelevant(self, func_addr):
        # function directories
        if len(self.sel_dirs) and self.sel_dirs[0] != '':
            if not (func_addr in self.clu_data['dirs'] and
                self.clu_data['dirs'][func_addr] in self.sel_dirs):
                return False
        # function name prefixes
        func_name = ida_shims.get_func_name(func_addr)
        func_prfx = ida_utils.get_func_prefs(func_name, True)
        if len(self.sel_prfx) and self.sel_prfx[0] != '':
            if self.ui.wPrefixFilter.getState() == True:
                if len(func_prfx) != len(self.sel_prfx) or not all(p in self.sel_prfx for p in func_prfx):
                    return False
            else:
                if not any(p in self.sel_prfx for p in func_prfx):
                    return False
        # function highlight color
        func_colr = plg_utils.RgbColor(ida_shims.get_color(func_addr, idc.CIC_FUNC))
        func_colr.invert_color()

        if len(self.sel_colr) and self.sel_colr[0] != '':
            if not any(func_colr == plg_utils.RgbColor(cn) for cn in self.sel_colr):
                return False
        return True

    def treeDoubleClick(self, index):
        if not index.isValid():
            return None
        addr_index = index.sibling(index.row(), self.getFuncAddrCol())
        cell_data = addr_index.data()
        if cell_data and cell_data.startswith('0x'):
            idaapi.jumpto(plg_utils.from_hex(cell_data))

    def getLabelNorm(self, label_mode):
        label_name = None
        if label_mode == 'folder':
            label_name = self.ui.wLabelTool.getLabelName(prfx="/")
        elif label_mode == 'prefix':
            label_name = self.ui.wLabelTool.getLabelName(sufx="_")
        return label_name

    def updateFilters(self, label_mode, changelog):
        if label_mode == 'folder':
            fback = self.ui.wFolderFilter.chgItems(changelog, is_sorted=True)
            for fdir in fback:
                ida_utils.remove_dir(fdir)
        elif label_mode == 'prefix':
            self.ui.wPrefixFilter.chgItems(changelog, is_sorted=True)
        elif label_mode == 'color':
            self.ui.wColorFilter.chgItems(changelog, is_sorted=True)

    def isDataSelected(self):
        return self.ui.rvTable.selectionModel().hasSelection()

    def addLabel(self):
        if self.isDataSelected():
            label_mode = self.ui.wLabelTool.getLabelMode()
            label_norm = self.getLabelNorm(label_mode)

            if self.env_desc.feat_folders and label_mode == 'folder':
                ida_utils.create_dir(label_norm, is_abs=True)

            addr_queue = self.getLabelAddrSet()
            changelog = {
                'sub': collections.defaultdict(int),
                'add': collections.defaultdict(int),
            }

            model = self.ui.rvTable.model()
            name_col = self.ui.rvTable.heads.index('Name')
            fldr_col = self.ui.rvTable.heads.index('Folder')

            for func_addr in addr_queue:
                func_name = ida_shims.get_func_name(func_addr)
                for id_group, id_child in self.ui.rvTable.rec_indx[func_addr]:
                    if label_mode == 'prefix':
                        if not re.match("{0}%|{0}_".format(label_norm[:-1]), func_name):
                            func_name_new = plg_utils.add_prefix(func_name, label_norm, False)
                            ida_shims.set_name(func_addr, func_name_new, idaapi.SN_CHECK)
                            indx_child = model.index(id_child, name_col, model.index(id_group, 0))
                            model.layoutAboutToBeChanged.emit()
                            model.setData(indx_child, func_name_new)
                            model.layoutChanged.emit()
                            for tkn in label_norm.split('_'):
                                if tkn != '':
                                    changelog['add'][tkn] += 1
                    elif label_mode == 'folder':
                        folder_src = self.clu_data['dirs'].get(func_addr, '/')
                        if label_norm != folder_src:
                            self.clu_data['dirs'][func_addr] = label_norm
                            changelog['sub'][folder_src] += 1
                            changelog['add'][label_norm] += 1
                            ida_utils.set_func_folder(func_addr, folder_src, label_norm)
                            indx_child = model.index(id_child, fldr_col, model.index(id_group, 0))
                            model.layoutAboutToBeChanged.emit()
                            model.setData(indx_child, label_norm)
                            model.layoutChanged.emit()
                    else:
                        ida_shims.msg('ERROR: unknown label mode')
                        return

            if len(changelog['sub']) or len(changelog['add']):
                self.updateFilters(label_mode, changelog)
            if self.env_desc.ver_py > 2:
                ida_utils.refresh_ui()

    def clsLabel(self):
        if self.ui.rvTable.selectionModel().hasSelection():
            indexes = [index for index in self.ui.rvTable.selectionModel().selectedRows()]
            data = [index.sibling(index.row(), self.getFuncAddrCol()).data() for index in indexes]
            changelog = {
                'sub': collections.defaultdict(int),
                'add': collections.defaultdict(int),
            }

            model = self.ui.rvTable.model()
            name_col = self.ui.rvTable.heads.index('Name')
            fldr_col = self.ui.rvTable.heads.index('Folder')

            for idx, addr_field in enumerate(set(data)):
                func_addr = int(addr_field, base=16)
                func_name = ida_shims.get_func_name(func_addr)
                for id_group, id_child in self.ui.rvTable.rec_indx[func_addr]:
                    label_mode = self.ui.wLabelTool.getLabelMode()
                    if label_mode == 'prefix':
                        func_prefs = ida_utils.get_func_prefs(func_name, True)
                        last_pref = func_prefs[0]
                        if len(func_prefs) >= 1 and last_pref != 'sub':
                            func_name_new = re.sub('{0}%|{0}_'.format(last_pref), '', func_name, 1)
                            # cleanup in case of next bad prefix in front
                            func_name_new = ida_utils.get_cleaned_funcname(func_name_new)
                            ida_shims.set_name(func_addr, func_name_new, idaapi.SN_NOWARN)
                            indx_child = model.index(id_child, name_col, model.index(id_group, 0))
                            model.layoutAboutToBeChanged.emit()
                            model.setData(indx_child, func_name_new)
                            model.layoutChanged.emit()
                            changelog['sub'][last_pref] += 1
                    elif label_mode == 'folder':
                        func_fldr = self.clu_data['dirs'].get(func_addr, '/')
                        changelog['sub'][func_fldr] += 1
                        changelog['add']['/'] += 1
                        ida_utils.set_func_folder(func_addr, func_fldr, '/')
                        indx_child = model.index(id_child, fldr_col, model.index(id_group, 0))
                        model.layoutAboutToBeChanged.emit()
                        model.setData(indx_child, '/')
                        model.layoutChanged.emit()
                        self.clu_data['dirs'][func_addr] = '/'
                    else:
                        ida_shims.msg('ERROR: unknown label mode')
                        return
            self.updateFilters(label_mode, changelog)
            if self.env_desc.ver_py > 2:
                ida_utils.refresh_ui()

    def showContextMenu(self, point):
        ix = self.ui.rvTable.indexAt(point)
        if ix.column() == 0:
            menu = QMenu()
            renameAction = menu.addAction(QIcon(':/idaclu/icon_64.png'), i18n("Rename"))
            action = menu.exec_(self.ui.rvTable.mapToGlobal(point))
            if action == renameAction:
                self.ui.rvTable.edit(ix)

    def getFuncAddrCol(self):
        if self.env_desc.feat_folders:
            return 2
        else:
            return 1

    def changeFuncColor(self):
        if self.isDataSelected():
            sender_button = self.sender()
            btn_name = sender_button.objectName()
            color_set = None
            if btn_name == 'SetColorBlue':
                color_set = plg_utils.RgbColor((199,255,255), 'blue')
            elif btn_name == 'SetColorYellow':
                color_set = plg_utils.RgbColor((255,255,191), 'yellow')
            elif btn_name == 'SetColorGreen':
                color_set = plg_utils.RgbColor((191,255,191), 'green')
            elif btn_name == 'SetColorPink':
                color_set = plg_utils.RgbColor((255,191,239), 'pink')
            elif btn_name == 'SetColorNone':
                color_set = plg_utils.RgbColor((255,255,255), 'none')
            else:
                ida_shims.msg('ERROR: unknown palette button')

            addr_queue = self.getLabelAddrSet()

            changelog = {
                'sub': collections.defaultdict(int),
                'add': collections.defaultdict(int),
            }

            model = self.ui.rvTable.model()
            id_col = self.ui.rvTable.heads.index('Color')

            for func_addr in addr_queue:
                for id_group, id_child in self.ui.rvTable.rec_indx[func_addr]:
                    color_get = plg_utils.RgbColor(ida_shims.get_color(func_addr, idc.CIC_FUNC))
                    color_get.invert_color()
                    ida_shims.set_color(func_addr, idc.CIC_FUNC, color_set.get_to_int(True))
                    indx_child = model.index(id_child, id_col, model.index(id_group, 0))
                    model.layoutAboutToBeChanged.emit()
                    model.setData(indx_child, color_set.get_to_str())
                    model.layoutChanged.emit()

                    changelog['sub'][color_get.get_to_name()] += 1
                    changelog['add'][color_set.get_to_name()] += 1
            self.updateFilters('color', changelog)
            if self.env_desc.ver_py > 2:
                ida_utils.refresh_ui()

    def getLabelAddrSet(self):
        id_col = self.ui.rvTable.heads.index('Address')
        indexes = [idx for idx in self.ui.rvTable.selectionModel().selectedRows()]
        fields = [idx.sibling(idx.row(), id_col).data() for idx in indexes]

        addr_queue = set()
        for idx, field in enumerate(fields):
            func_addr = int(field, base=16)
            addr_queue.add(func_addr)

        addr_calees = set()
        if self.is_mode_recursion == True:
            for func_addr in addr_queue:
                addr_calees.update(ida_utils.recursive_prefix(func_addr))

        addr_queue.update(addr_calees)
        return addr_queue

    def swapPosition(self, reset=False):
        layout = self.ui.DialogSplitter

        layout_sizes = None
        if reset:
            layout_width = layout.width()
            l_size = int(layout_width * 0.3)
            r_size = int(layout_width * 0.7)
            layout_sizes = [l_size, r_size] if self.is_sidebar_on_left else [r_size, l_size]
        else:
            layout_sizes = layout.sizes()
            layout_sizes = layout_sizes[::-1]

        self.ui.SidebarFrame.setParent(None)
        self.ui.MainFrame.setParent(None)

        if self.is_sidebar_on_left:
            layout.insertWidget(0, self.ui.MainFrame)
            layout.insertWidget(1, self.ui.SidebarFrame)
        else:
            layout.insertWidget(0, self.ui.SidebarFrame)
            layout.insertWidget(1, self.ui.MainFrame)

        # layout.setCollapsible(0,False)
        # layout.setCollapsible(1,False)
        layout.setSizes(layout_sizes)

        self.is_sidebar_on_left = not self.is_sidebar_on_left

    def showFilters(self):
        if not self.is_filters_shown:
            self.ui.FiltersGroup.setMinimumSize(QSize(16777215, 16777215))
            self.ui.FiltersGroup.setMaximumSize(QSize(16777215, 16777215))
        else:
            self.ui.FiltersGroup.setMinimumSize(QSize(16777215, 1))
            self.ui.FiltersGroup.setMaximumSize(QSize(16777215, 1))

        self.is_filters_shown = not self.is_filters_shown

    def get_splg_root(self, plg_path, plg_fldr):
        splg_root = os.path.join(plg_path, plg_fldr, 'plugins')
        return splg_root

    def get_splg_tree(self, plg_splg_path):
        plg_tree = {}
        if os.path.exists(plg_splg_path):
            plg_tree = plg_utils.get_ordered_folder_tree(plg_splg_path)
        return plg_tree

    def is_sp_fname(self, sp_fname):
        return sp_fname.startswith('plugin_') and sp_fname.endswith('.py') and sp_fname != '__init__.py'

    def get_sp_controls(self, sp_path):
        sp_tree = self.get_splg_tree(sp_path)

        # depth of folder tree containing plugins is known
        for gdx, spg_ref in enumerate(sp_tree):
            if len(sp_tree[spg_ref]):
                spg_path = str(os.path.join(sp_path, spg_ref))
                spg_name = getattr(plg_utils.import_path(spg_path), 'PLUGIN_GROUP_NAME')
                spg_title = '{}. {}'.format(str(gdx+1), spg_name)

                spg_layout = FrameLayout(title=spg_title, env=self.env_desc)
                spg_layout.setProperty('class', 'frame')
                for sp_fname in sp_tree[spg_ref]:
                    plg_btn = None
                    if not self.is_sp_fname(sp_fname):
                        continue
                    sp_bname = sp_fname.replace('.py', '')
                    sp_name = sp_bname
                    # initial name is equal to file base name
                    # in case name will be not defined in plugin

                    sp_module = None
                    spe_msg = ""
                    # make sub-plugin discoverable in its group for importing
                    with plg_utils.PluginPath(os.path.join(sp_path, spg_ref)):
                        is_plug_ok = False
                        try:
                            sp_module = __import__(sp_bname)
                            del sys.modules[sp_bname]
                        except ImportError as err:
                            # in case some dependency is sub-plugin is missing
                            # the corresponding button will be disabled and
                            # tooltip will show this error
                            module_name = None
                            if self.env_desc.ver_py == 3:
                                module_name = err.name
                            else:
                                module_name = err.args[0].rsplit(' ',1)[-1]  # there is no .name attribute for Python2
                            spe_msg = "Module not found: {}".format(module_name)
                            # Attempt to open the module as a text file
                            # at least to recover sub-plugin name
                            try:
                                with open(os.path.join(sp_path, spg_ref, sp_fname), 'r') as file:
                                    for line in file:
                                        match = re.search(r'SCRIPT_NAME\s*=\s*["\']([^"\']+)', line)
                                        if match:
                                            sp_name = match.group(1)
                                            # self.log.debug("Recovered SCRIPT_NAME:", sp_name)
                                            break
                                    else:
                                        pass
                                        # self.log.debug("SCRIPT_NAME definition was not found")
                            except FileNotFoundError:
                                pass
                                # self.log.debug("Module file not found")
                        else:
                            is_plug_ok = True

                    # an attempt to load sub-plugin finished
                    # let's draw a corresponding button
                    sp_name = getattr(sp_module, 'SCRIPT_NAME', sp_name)
                    sp_layout = QVBoxLayout()
                    sp_frame = QFrame()
                    sp_frame.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
                    sp_frame.setObjectName('Frame#{}#{}'.format(spg_ref, sp_bname))

                    sp_button = QPushButton(sp_name)
                    if is_plug_ok:
                        sp_button.clicked.connect(self.get_plugin_data)
                    else:
                        sp_button.setEnabled(False)
                        sp_button.setToolTip(spe_msg)

                    sp_button.setObjectName('Button#{}#{}'.format(spg_ref, sp_bname))
                    sp_layout.addWidget(sp_button)
                    sp_frame.setLayout(sp_layout)
                    spg_layout.addWidget(sp_frame)
                yield spg_layout
