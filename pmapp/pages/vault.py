"""The Vault page: your items on the left, the one you picked on the right (to read or to edit)."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from .. import icons, model
from ..theme import C
from ..widgets import EmptyState, ItemDelegate, button, label
from .items import ItemEditor, ItemView, ShareDialog

SORTS = (("name", "Name"), ("recent", "Recently changed"), ("weak", "Needs attention first"))


class DetailPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("detail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.empty = EmptyState("vault", "Select an item", "Pick something from the list, or press New to add a login, card, secure note or Wi-Fi network.")
        self.view = ItemView(win)
        self.editor = ItemEditor(win)
        for w in (self.empty, self.view, self.editor):
            self.stack.addWidget(w)
        v.addWidget(self.stack)

    @property
    def editing(self):
        return self.stack.currentWidget() is self.editor


class VaultPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.f_kind = None
        self.f_group = None
        self.f_fav = False
        self.sort = "name"
        self.current = None
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(18)
        left = QWidget()
        left.setObjectName("listcol")
        left.setFixedWidth(364)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search your vault")
        self.search.setClearButtonEnabled(True)
        act = QAction(icons.icon("search", C["muted"], 17), "", self.search)
        self.search.addAction(act, QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search, 1)
        self.b_new = button("New", "primary", None, "plus")
        menu = QMenu(self.b_new)
        for kind in model.KINDS:
            a = menu.addAction(icons.icon({"login": "key", "card": "card", "note": "note", "wifi": "wifi"}[kind], C["soft"], 18), model.KIND_LABEL[kind])
            a.triggered.connect(lambda _=False, k=kind: win.new_item(k))
        self.b_new.setMenu(menu)
        top.addWidget(self.b_new)
        lv.addLayout(top)
        head = QHBoxLayout()
        self.title = label("All items", "h2")
        self.count = label("", "faint")
        head.addWidget(self.title)
        head.addSpacing(4)
        head.addWidget(self.count)
        head.addStretch(1)
        self.b_sort = button("Name", "ghost", self.cycle_sort, "chevron_down")
        self.b_sort.setLayoutDirection(Qt.RightToLeft)
        head.addWidget(self.b_sort)
        lv.addLayout(head)
        self.list = QListWidget()
        self.list.setItemDelegate(ItemDelegate(self.list, lambda e: (e["group"], e["title"]) in win.health))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.currentItemChanged.connect(self.on_select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context)
        lv.addWidget(self.list, 1)
        self.empty_list = EmptyState("search", "Nothing here", "No items match. Try another search, or add a new one.")
        self.empty_list.hide()
        lv.addWidget(self.empty_list, 1)
        root.addWidget(left)
        self.detail = DetailPanel(win)
        self.detail.view.edit.connect(self.edit_current)
        self.detail.view.delete.connect(self.delete_current)
        self.detail.view.share.connect(self.share_current)
        self.detail.view.favorite.connect(self.toggle_fav)
        self.detail.editor.saved.connect(win.save_item)
        self.detail.editor.cancelled.connect(self.cancel_edit)
        root.addWidget(self.detail, 1)

    # -- filters
    def set_filter(self, kind=None, group=None, fav=False):
        self.f_kind, self.f_group, self.f_fav = kind, group, fav
        self.refresh()

    def cycle_sort(self):
        keys = [k for k, _ in SORTS]
        self.sort = keys[(keys.index(self.sort) + 1) % len(keys)]
        self.b_sort.setText(dict(SORTS)[self.sort])
        self.refresh()

    def shown(self):
        q = self.search.text().strip().lower()
        out = []
        for e in self.win.entries:
            k = model.kind_of(e)
            d = e.get("data") or {}
            if self.f_kind and k != self.f_kind:
                continue
            if self.f_group is not None and e["group"] != self.f_group:
                continue
            if self.f_fav and not d.get("fav"):
                continue
            if q:
                extra = " ".join(str(d.get(x, "")) for x in ("ssid", "holder")) + (" " + model.card_brand(d.get("number", "")) if k == "card" else "")
                if q not in " ".join((e["title"], e["username"], e["url"], e["notes"], e["group"], extra)).lower():
                    continue
            out.append(e)
        if self.sort == "recent":
            out.sort(key=lambda e: e.get("modified", ""), reverse=True)
        elif self.sort == "weak":
            out.sort(key=lambda e: ((e["group"], e["title"]) not in self.win.health, e["title"].lower()))
        else:
            out.sort(key=lambda e: e["title"].lower())
        return out

    def refresh(self, *_):
        if not self.win.vault:
            return
        cur = self.current
        items = self.shown()
        self.list.blockSignals(True)
        self.list.clear()
        sel = None
        for e in items:
            it = QListWidgetItem(e["title"])
            it.setData(Qt.UserRole, e)
            self.list.addItem(it)
            if cur and (e["group"], e["title"]) == (cur["group"], cur["title"]):
                sel = it
        self.list.blockSignals(False)
        name = (model.KIND_PLURAL[self.f_kind] if self.f_kind else "Favorites" if self.f_fav else self.f_group if self.f_group is not None else "All items")
        self.title.setText(name)
        self.count.setText(f"{len(items)}")
        self.list.setVisible(bool(items))
        self.empty_list.setVisible(not items)
        if sel is not None and not self.detail.editing:
            self.list.setCurrentItem(sel)
        elif not self.detail.editing and not (cur and sel):
            self.show_none()

    # -- selection
    def show_none(self):
        self.current = None
        self.detail.stack.setCurrentWidget(self.detail.empty)

    def on_select(self, cur, _prev):
        if cur is None or self.detail.editing:
            return
        self.show(cur.data(Qt.UserRole))

    def show(self, e):
        self.current = e
        self.detail.view.show_item(e)
        self.detail.stack.setCurrentWidget(self.detail.view)

    def select(self, e):
        for i in range(self.list.count()):
            x = self.list.item(i).data(Qt.UserRole)
            if (x["group"], x["title"]) == (e["group"], e["title"]):
                self.list.setCurrentRow(i)
                self.show(x)                     # the row may already have been the current one, which sends no signal
                return True
        return False

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    # -- editing
    def start_new(self, kind, **preset):
        e = model.new_item(kind, self.win.current_group_for(kind))
        e.update(preset)
        self.list.clearSelection()
        self.detail.editor.load(e, new=True)
        self.detail.stack.setCurrentWidget(self.detail.editor)

    def edit_current(self):
        if self.current:
            self.detail.editor.load(self.current)
            self.detail.stack.setCurrentWidget(self.detail.editor)

    def cancel_edit(self):
        if self.current:
            self.show(self.current)
        else:
            self.show_none()

    def finish_edit(self, e=None):
        self.detail.stack.setCurrentWidget(self.detail.empty)
        self.current = e
        self.refresh()
        if e is not None:
            if not self.select(e):
                self.show(e)

    def delete_current(self):
        e = self.current
        if e and QMessageBox.question(self, "Delete", f"Delete “{e['title']}”? It goes to the vault's recycle bin.") == QMessageBox.Yes:
            self.win.delete_item(e)

    def share_current(self):
        e = self.current
        if e:
            ShareDialog(self.win, [e], f"“{e['title']}”").exec()

    def toggle_fav(self):
        e = self.current
        if not e:
            return
        new = dict(e, data=dict(e.get("data") or {}))
        if new["data"].get("fav"):
            new["data"].pop("fav")
        else:
            new["data"]["fav"] = True
        self.win.save_item(e, new, quiet=True)

    def context(self, pos: QPoint):
        it = self.list.itemAt(pos)
        if not it:
            return
        e = it.data(Qt.UserRole)
        m = QMenu(self)
        k = model.kind_of(e)
        if k == "login" and e.get("username"):
            m.addAction("Copy username").triggered.connect(lambda: self.win.copy_plain(e["username"], "Username"))
        if e.get("password"):
            m.addAction("Copy password" if k != "wifi" else "Copy Wi-Fi password").triggered.connect(lambda: self.win.copy_secret(e["password"]))
        if k == "card" and (e.get("data") or {}).get("number"):
            m.addAction("Copy card number").triggered.connect(lambda: self.win.copy_secret(e["data"]["number"]))
        m.addSeparator()
        m.addAction("Edit").triggered.connect(lambda: (self.list.setCurrentItem(it), self.edit_current()))
        m.addAction("Delete").triggered.connect(lambda: (self.list.setCurrentItem(it), self.delete_current()))
        m.exec(self.list.mapToGlobal(pos))
