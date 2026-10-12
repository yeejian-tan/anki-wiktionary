from __future__ import annotations

import os
import re
import shutil
import sys
import traceback
from concurrent.futures import Future

from aqt.main import AnkiQt
from aqt.qt import QComboBox, QHBoxLayout, QKeySequence, QLabel, QPushButton, QWidget, qconnect
from aqt.utils import askUser, getFile, openLink, showWarning, tooltip

from ..consts import consts
from ..fetcher import WiktionaryFetcher
from ..forms.importer import Ui_Dialog
from ..gui.dialog import Dialog
from ..utils import get_dict_names, get_dicts


class ImportDictionaryDialog(Dialog):
    key = "importer"

    def __init__(
        self,
        mw: AnkiQt,
    ):
        self.mw = mw
        super().__init__(mw)
        self.errors: list[str] = []

    def setup_ui(self) -> None:
        self.form = Ui_Dialog()
        self.form.setupUi(self)
        qconnect(
            self.form.chooseFileButton.clicked,
            self.on_choose_file,
        )
        qconnect(self.form.addButton.clicked, self.on_add)
        self.form.addButton.setShortcut(QKeySequence("Ctrl+Return"))
        self.form.description.setText(
            """
Here you can import a new dictionary downloaded from \
<a href="https://kaikki.org/dictionary/">the dictionary list at kaikki.org</a>.<br>
After finding your language link in the list and opening it, you'll find an option<br>
at the bottom of the page to download a JSON file (which has a name like "kaikki.org-dictionary-Russian.json")<br>
containing all word senses that you can import to Anki here.<br>
The imported dictionary will be made available for use in the add-on's main dialog.<br>
To use it, select notes in the browser (or open a note in the editor) and use the add-on's shortcut,<br>
which opens a dialog where you pick the dictionary and the fields to fill.<br>
Importing a file with the name of an existing dictionary replaces that dictionary."""
        )
        self._setup_existing_dicts()
        qconnect(
            self.form.description.linkActivated,
            lambda link: openLink(link),
        )
        super().setup_ui()

    def _setup_existing_dicts(self) -> None:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Existing dictionaries:", row))
        self.existing_combo = QComboBox(row)
        layout.addWidget(self.existing_combo, 1)
        self.delete_button = QPushButton("Delete", row)
        layout.addWidget(self.delete_button)
        qconnect(self.delete_button.clicked, self.on_delete)
        self.form.verticalLayout.insertWidget(1, row)
        self._refresh_existing_dicts()

    def _refresh_existing_dicts(self) -> None:
        self.existing_combo.clear()
        names = sorted(get_dict_names(), key=str.lower)
        self.existing_combo.addItems(names or ["(none)"])
        self.existing_combo.setEnabled(bool(names))
        self.delete_button.setEnabled(bool(names))

    def on_delete(self) -> None:
        name = self.existing_combo.currentText()
        if not askUser(f'Delete the dictionary "{name}"? You can import it again later.', parent=self):
            return
        for path in get_dicts():
            if path.stem == name:
                path.unlink()
        self._refresh_existing_dicts()

    def on_choose_file(self) -> None:
        filename = getFile(
            self,
            title=consts.name,
            cb=None,
            key=consts.name,
        )
        if not filename:
            return
        filename = str(filename)
        self.form.filenameLabel.setText(filename)
        name_match = re.search(r"kaikki.org-dictionary-(.*?)\.", filename)
        if name_match:
            name = name_match.group(1)
        else:
            name, _ = os.path.splitext(os.path.basename(filename))
        self.form.dictionaryNameLineEdit.setText(name)

    def on_add(self) -> None:
        want_cancel = False

        def on_progress(count: int) -> bool:
            def update() -> None:
                self.mw.progress.update(f"Imported {count} words...")
                nonlocal want_cancel
                want_cancel = self.mw.progress.want_cancel()

            self.mw.taskman.run_on_main(update)
            return not want_cancel

        def on_error(line: int, exc: Exception) -> None:
            self.errors.append(f"failed to process line {line}: {str(exc)}")

        # Import into a temporary folder first, so a failed or cancelled import doesn't destroy an existing dictionary
        tmp_dir = consts.dicts_dir / "_importing"

        def on_done(future: Future) -> None:
            self.mw.progress.finish()
            try:
                count = future.result()
                if want_cancel:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return
                os.replace(tmp_dir / f"{name}.db", consts.dicts_dir / f"{name}.db")
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as exc:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                traceback.print_exception(None, exc, exc.__traceback__, file=sys.stdout)
                showWarning(str(exc), parent=self, title=consts.name)
                return
            tooltip(f"Successfully imported {count} words", parent=self.mw)
            self.accept()

        filename = self.form.filenameLabel.text()
        name = self.form.dictionaryNameLineEdit.text().strip()
        if not name or not filename:
            showWarning("Filename and dictionary name fields cannot be empty")
            return
        if name in get_dict_names() and not askUser(
            f'A dictionary named "{name}" already exists. Replace it?', parent=self
        ):
            return
        shutil.rmtree(tmp_dir, ignore_errors=True)
        self.mw.progress.start(label="Starting importing...", parent=self)
        self.mw.progress.set_title(f"{consts.name} - Importing a dictionary")
        self.mw.taskman.run_in_background(
            lambda: WiktionaryFetcher.import_kaikki_dict(
                filename,
                name,
                on_progress=on_progress,
                on_error=on_error,
                base_dir=tmp_dir,
            ),
            on_done=on_done,
        )
