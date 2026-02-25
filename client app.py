import sys
import json
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QFrame, QSizePolicy, QTextEdit, QScrollArea,
    QDialog, QFormLayout, QDialogButtonBox, QMessageBox
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont, QColor, QPalette, QTextCharFormat, QBrush

# File paths for persistence
CONTACTS_FILE = "contacts.json"
MESSAGES_FILE = "messages.json"

class ContactItem(QListWidgetItem):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.original_name = name
        self.setText(f"  •  {name}")
        self.setSizeHint(QSize(0, 52))

    def set_highlight(self, search_text):
        if not search_text:
            self.setText(f"  •  {self.original_name}")
            return

        text = self.original_name.lower()
        search = search_text.lower()
        if search not in text:
            self.setHidden(True)
            return

        self.setHidden(False)
        new_text = "  •  "
        pos = 0
        fmt_normal = QTextCharFormat()
        fmt_highlight = QTextCharFormat()
        fmt_highlight.setFontWeight(QFont.Bold)
        fmt_highlight.setForeground(QBrush(QColor("#58a6ff")))

        while True:
            start = text.find(search, pos)
            if start == -1:
                new_text += self.original_name
                break
            pos = start + len(search)

        self.setText(new_text)


class Message:
    def __init__(self, text, is_sent, timestamp=None):
        self.text = text
        self.is_sent = is_sent
        self.timestamp = timestamp or "just now"  # you can use datetime later


class MessageWidget(QWidget):
    def __init__(self, msg: Message, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(0)

        bubble = QLabel(msg.text)
        bubble.setWordWrap(True)
        bubble.setStyleSheet(f"""
            QLabel {{
                background: {'#2b5278' if msg.is_sent else '#3a3f44'};
                color: {'#f0f6fc' if msg.is_sent else '#d1d5db'};
                padding: 10px 16px;
                border-radius: 18px;
                border-top-right-radius: {'18px' if msg.is_sent else '4px'};
                border-top-left-radius: {'4px' if msg.is_sent else '18px'};
                max-width: 66%;
            }}
        """)
        bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        if msg.is_sent:
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            layout.addWidget(bubble)
            layout.addStretch()


class AddContactDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        #/* border:1px solid #30363d; */
        #/* border-radius:20px; */
        style2 ="""border:1px solid #30363d;
        border-radius:20px;"""

        stlye = """background:#0d1117;
        color:#e6edf3;
        padding:10px 16px;
        font-size:14px;"""

        self.setWindowTitle("Add New Contact")
        self.setFixedSize(380, 220)
        self.setStyleSheet(stlye)
        
        layout = QFormLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Contact name *")
        self.name_edit.setStyleSheet(style2)

        layout.addRow("Name:", self.name_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.setStyleSheet(style2)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setText("Add")
        layout.addRow(buttons)

    def get_name(self):
        return self.name_edit.text().strip()


class ModernChatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chat – John")
        self.resize(960, 640)
        self.setMinimumSize(760, 520)

        self.contacts = []
        self.messages = {}  # contact_name → list of Message objects

        self.load_data()

        # UI setup (mostly same as before, only changed parts shown below)
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar (username, add, settings, search, contact list)
        sidebar = QFrame()
        sidebar.setFixedWidth(300)
        sidebar.setStyleSheet("background: #161b22; border-right: 1px solid #21262d;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 16, 16, 16)
        sidebar_layout.setSpacing(12)

        header = QHBoxLayout()
        name_lbl = QLabel("John")
        name_lbl.setStyleSheet("font-size: 19px; font-weight: bold; color: #e6edf3;")
        add_btn = QPushButton("+")
        add_btn.setFixedSize(36, 36)
        add_btn.setStyleSheet("QPushButton {background:#21262d; border:1px solid #30363d; border-radius:18px; color:#58a6ff; font-size:20px; font-weight:bold;} QPushButton:hover {background:#30363d; color:#79c0ff;} QPushButton:pressed {background:#1f6feb; color:white;}")
        add_btn.clicked.connect(self.open_add_contact)

        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setStyleSheet("QPushButton {background:#21262d; border:1px solid #30363d; border-radius:18px; color:#8b949e; font-size:18px;} QPushButton:hover {background:#30363d; color:#c9d1d9;} QPushButton:pressed {background:#444c56;}")
        # settings_btn.clicked.connect(self.open_settings)  # you can re-add later

        header.addWidget(name_lbl)
        header.addStretch()
        header.addWidget(add_btn)
        header.addWidget(settings_btn)
        sidebar_layout.addLayout(header)

        # Search
        search_frame = QFrame()
        search_frame.setStyleSheet("background: #0d1117; border: 1px solid #30363d; border-radius: 10px;")
        search_lay = QHBoxLayout(search_frame)
        search_lay.setContentsMargins(12, 8, 12, 8)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("color: #8b949e;")

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search contacts...")
        self.search_edit.setStyleSheet("QLineEdit {background:transparent; border:none; color:#e6edf3; font-size:14px;} QLineEdit:focus {background:#161b22;}")
        self.search_edit.textChanged.connect(self.on_search_changed)

        search_lay.addWidget(search_icon)
        search_lay.addWidget(self.search_edit)
        sidebar_layout.addWidget(search_frame)

        # Contacts list
        self.contact_list = QListWidget()
        self.contact_list.setStyleSheet("""
            QListWidget {background: transparent; border: none;}
            QListWidget::item {padding: 11px 14px; border-radius: 8px; color: #c9d1d9; background: #161b22; margin: 1px 0;}
            QListWidget::item:selected {background: #1f6feb; color: white;}
            QListWidget::item:hover:!selected {background: #21262d;}
        """)
        self.contact_list.setSpacing(2)
        sidebar_layout.addWidget(self.contact_list, 1)

        self.populate_contacts()

        # Chat area (same structure)
        self.chat_frame = QFrame()
        self.chat_frame.setStyleSheet("background: #0d1117;")
        self.chat_layout = QVBoxLayout(self.chat_frame)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(0)

        self.placeholder_label = QLabel("Select a contact or start a new chat")
        self.placeholder_label.setAlignment(Qt.AlignCenter)
        self.placeholder_label.setStyleSheet("color: #6e7681; font-size: 17px;")
        self.chat_layout.addWidget(self.placeholder_label, 1)

        self.messages_scroll = QScrollArea()
        self.messages_scroll.setWidgetResizable(True)
        self.messages_scroll.setVisible(False)
        self.messages_scroll.setStyleSheet("QScrollArea {border: none;}")

        self.messages_container = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(12, 20, 12, 20)
        self.messages_layout.setSpacing(6)
        self.messages_layout.addStretch()
        self.messages_scroll.setWidget(self.messages_container)
        self.chat_layout.addWidget(self.messages_scroll, 1)

        self.input_frame = QFrame()
        self.input_frame.setVisible(False)
        self.input_frame.setStyleSheet("background: #161b22; border-top: 1px solid #21262d;")
        input_layout = QHBoxLayout(self.input_frame)
        input_layout.setContentsMargins(16, 12, 16, 12)

        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("Type a message...")
        self.message_input.setStyleSheet("QTextEdit {background:#0d1117; border:1px solid #30363d; border-radius:20px; color:#e6edf3; padding:10px 16px; font-size:14px;}")
        self.message_input.setMaximumHeight(140)
        self.message_input.setMinimumHeight(48)

        send_btn = QPushButton("➤")
        send_btn.setFixedSize(48, 48)
        send_btn.setStyleSheet("QPushButton {background:#1f6feb; color:white; border:none; border-radius:24px; font-size:22px;} QPushButton:hover {background:#388bfd;} QPushButton:pressed {background:#0051cc;}")
        send_btn.clicked.connect(self.send_message)

        input_layout.addWidget(self.message_input)
        input_layout.addWidget(send_btn)
        self.chat_layout.addWidget(self.input_frame)

        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.chat_frame, 1)

        self.contact_list.currentItemChanged.connect(self.on_contact_selected)
        self.message_input.textChanged.connect(self.adjust_input_height)

        self.current_contact = None

    def adjust_input_height(self):
        doc_h = self.message_input.document().size().height()
        new_h = min(max(48, int(doc_h + 32)), 140)
        self.message_input.setFixedHeight(new_h)

    def populate_contacts(self):
        self.contact_list.clear()
        for name in self.contacts:
            item = ContactItem(name)
            self.contact_list.addItem(item)

    def on_search_changed(self, text):
        text = text.strip()
        if text:
            for i in range(self.contact_list.count()):
                item = self.contact_list.item(i)
                if isinstance(item, ContactItem):
                    item.set_highlight(text)
        else:
            self.populate_contacts()

    def on_contact_selected(self, current, previous):
        if not current:
            self.current_contact = None
            self.placeholder_label.setVisible(True)
            self.messages_scroll.setVisible(False)
            self.input_frame.setVisible(False)
            self.clear_messages()
            return

        self.current_contact = current.original_name
        self.placeholder_label.setVisible(False)
        self.messages_scroll.setVisible(True)
        self.input_frame.setVisible(True)
        self.clear_messages()
        self.load_messages_for_current()
        self.scroll_to_bottom()

    def load_messages_for_current(self):
        if not self.current_contact or self.current_contact not in self.messages:
            return
        for msg in self.messages[self.current_contact]:
            widget = MessageWidget(msg)
            self.messages_layout.insertWidget(self.messages_layout.count()-1, widget)

    def add_message(self, text, is_sent=True):
        if not self.current_contact:
            return
        msg = Message(text, is_sent)
        if self.current_contact not in self.messages:
            self.messages[self.current_contact] = []
        self.messages[self.current_contact].append(msg)

        widget = MessageWidget(msg)
        self.messages_layout.insertWidget(self.messages_layout.count()-1, widget)
        self.save_messages()

    def send_message(self):
        text = self.message_input.toPlainText().strip()
        if not text or not self.current_contact:
            return
        self.add_message(text, True)
        self.message_input.clear()
        self.scroll_to_bottom()

        # Demo reply
        if text.lower().startswith(("hi", "hey", "hello")):
            reply = "Hey! How's it going? 😄"
            self.add_message(reply, False)
            self.scroll_to_bottom()

    def clear_messages(self):
        while self.messages_layout.count() > 1:
            w = self.messages_layout.takeAt(0).widget()
            if w:
                w.deleteLater()

    def scroll_to_bottom(self):
        vsb = self.messages_scroll.verticalScrollBar()
        vsb.setValue(vsb.maximum())

    def open_add_contact(self):
        dialog = AddContactDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            name = dialog.get_name()
            box = QMessageBox()
            
            if not name:
                box.setStyleSheet("""background:#0d1117;
                color:#e6edf3;
                padding:10px 16px;
                font-size:14px;""")
                box.warning(self, "Error", "Name cannot be empty.")
                return
            if name in self.contacts:
                box.information(self, "Info", "Contact already exists.")
                return
            self.contacts.append(name)
            self.messages[name] = []
            self.save_contacts()
            self.populate_contacts()
            box.information(self, "Success", f"Added contact: {name}")

    def load_data(self):
        if os.path.exists(CONTACTS_FILE):
            try:
                with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.contacts = data.get("contacts", [])
            except:
                self.contacts = []

        if os.path.exists(MESSAGES_FILE):
            try:
                with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    for contact, msgs in raw.items():
                        self.messages[contact] = [Message(m["text"], m["is_sent"], m.get("timestamp")) for m in msgs]
            except:
                self.messages = {}

    def save_contacts(self):
        data = {"contacts": self.contacts}
        with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_messages(self):
        serializable = {}
        for contact, msgs in self.messages.items():
            serializable[contact] = [{"text": m.text, "is_sent": m.is_sent, "timestamp": m.timestamp} for m in msgs]
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)


def main():
    app = QApplication(sys.argv)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(15, 17, 23))
    palette.setColor(QPalette.WindowText, QColor(230, 237, 243))
    palette.setColor(QPalette.Base, QColor(13, 17, 23))
    palette.setColor(QPalette.Text, QColor(201, 209, 217))
    palette.setColor(QPalette.Button, QColor(22, 27, 34))
    palette.setColor(QPalette.ButtonText, QColor(201, 209, 217))
    app.setPalette(palette)

    window = ModernChatWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()