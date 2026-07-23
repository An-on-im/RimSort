#!/usr/bin/env python3
# translation_gui.py
"""
GUI-обёртка для translation_helper.py.
Запуск: python translation_gui.py
"""

import sys
import asyncio
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QCoreApplication
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTextEdit, QProgressBar,
    QGroupBox, QCheckBox, QLineEdit, QSpinBox, QFormLayout,
    QMessageBox, QFileDialog
)

# Импортируем функции из translation_helper.py
# Предполагаем, что файл лежит рядом
try:
    from translation_helper import (
        check_translation, show_all_stats, validate_translation,
        run_lupdate, run_lrelease, auto_translate_file, process_language,
        get_translation_config, set_translation_config, TranslationConfig,
        RetryConfig, TimeoutConfig
    )
except ImportError:
    print("Ошибка: translation_helper.py не найден в текущей папке.")
    sys.exit(1)


class AsyncWorker(QThread):
    """Поток для выполнения асинхронных задач (auto-translate, process)."""
    finished = Signal(bool, str)   # success, message
    log = Signal(str)              # текст для вывода

    def __init__(self, coro, *args, **kwargs):
        super().__init__()
        self.coro = coro
        self.args = args
        self.kwargs = kwargs

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.coro(*self.args, **self.kwargs))
            self.finished.emit(result, "Готово" if result else "Ошибка")
        except Exception as e:
            self.finished.emit(False, str(e))
        finally:
            loop.close()


class TranslationGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RimSort Translation Helper GUI")
        self.setMinimumSize(900, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Верхняя панель: выбор языка ---
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Язык:"))
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)  # можно ввести новый код
        self.lang_combo.addItem("(все языки)")  # специальный вариант для всех
        self._refresh_langs()
        top_layout.addWidget(self.lang_combo, 1)

        self.add_lang_btn = QPushButton("➕ Добавить язык")
        self.add_lang_btn.clicked.connect(self._add_new_language)
        top_layout.addWidget(self.add_lang_btn)

        layout.addLayout(top_layout)

        # --- Панель опций (API-ключи, таймауты) ---
        options_group = QGroupBox("Настройки перевода")
        options_layout = QFormLayout(options_group)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("API-ключ для DeepL/OpenAI (опционально)")
        options_layout.addRow("API Key:", self.api_key_edit)

        self.model_edit = QLineEdit("gpt-3.5-turbo")
        self.model_edit.setPlaceholderText("Модель OpenAI")
        options_layout.addRow("Модель:", self.model_edit)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(30)
        options_layout.addRow("Таймаут (с):", self.timeout_spin)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setValue(3)
        options_layout.addRow("Повторы:", self.retries_spin)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 20)
        self.concurrent_spin.setValue(5)
        options_layout.addRow("Параллельно:", self.concurrent_spin)

        self.dry_run_check = QCheckBox("Только просмотр (dry-run)")
        options_layout.addRow(self.dry_run_check)

        layout.addWidget(options_group)

        # --- Кнопки команд ---
        buttons_group = QGroupBox("Действия")
        buttons_layout = QVBoxLayout(buttons_group)

        row1 = QHBoxLayout()
        self.check_btn = QPushButton("Проверить (check)")
        self.check_btn.clicked.connect(self._run_check)
        self.stats_btn = QPushButton("Статистика (stats)")
        self.stats_btn.clicked.connect(self._run_stats)
        self.validate_btn = QPushButton("Валидировать (validate)")
        self.validate_btn.clicked.connect(self._run_validate)
        row1.addWidget(self.check_btn)
        row1.addWidget(self.stats_btn)
        row1.addWidget(self.validate_btn)
        buttons_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.update_btn = QPushButton("Обновить .ts (update-ts)")
        self.update_btn.clicked.connect(self._run_update)
        self.compile_btn = QPushButton("Скомпилировать (compile)")
        self.compile_btn.clicked.connect(self._run_compile)
        row2.addWidget(self.update_btn)
        row2.addWidget(self.compile_btn)
        buttons_layout.addLayout(row2)

        row3 = QHBoxLayout()
        self.service_combo = QComboBox()
        self.service_combo.addItems(["google", "deepl", "openai"])
        self.auto_translate_btn = QPushButton("Авто-перевод (auto-translate)")
        self.auto_translate_btn.clicked.connect(self._run_auto_translate)
        self.process_btn = QPushButton("Полный цикл (process)")
        self.process_btn.clicked.connect(self._run_process)
        row3.addWidget(QLabel("Сервис:"))
        row3.addWidget(self.service_combo)
        row3.addWidget(self.auto_translate_btn)
        row3.addWidget(self.process_btn)
        buttons_layout.addLayout(row3)

        layout.addWidget(buttons_group)

        # --- Прогресс-бар и вывод ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFontFamily("monospace")
        layout.addWidget(self.output)

        # --- Статус ---
        self.statusBar().showMessage("Готов")

    def _refresh_langs(self):
        """Обновить список языков из папки locales."""
        current = self.lang_combo.currentText()
        self.lang_combo.clear()
        self.lang_combo.addItem("(все языки)")
        locales_dir = Path("locales")
        if locales_dir.exists():
            for ts in sorted(locales_dir.glob("*.ts")):
                self.lang_combo.addItem(ts.stem)
        # восстановить выбор, если был
        idx = self.lang_combo.findText(current)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)

    def _get_lang(self) -> Optional[str]:
        """Получить выбранный язык или None (все)."""
        text = self.lang_combo.currentText()
        if text == "(все языки)" or not text:
            return None
        return text

    def _get_config(self):
        """Собрать конфиг из полей."""
        config = TranslationConfig(
            retry_config=RetryConfig(max_retries=self.retries_spin.value()),
            timeout_config=TimeoutConfig(
                default_timeout=float(self.timeout_spin.value()),
                deepl_timeout=float(self.timeout_spin.value()),
                openai_timeout=float(self.timeout_spin.value()),
                google_timeout=float(self.timeout_spin.value()),
            ),
            max_concurrent_requests=self.concurrent_spin.value(),
            use_cache=True
        )
        return config

    def _get_service_kwargs(self):
        """Собрать аргументы для сервиса."""
        kwargs = {}
        api_key = self.api_key_edit.text().strip()
        if api_key:
            kwargs["api_key"] = api_key
        model = self.model_edit.text().strip()
        if model:
            kwargs["model"] = model
        return kwargs

    def _log(self, text):
        self.output.append(text)
        self.statusBar().showMessage(text[:100])

    def _run_sync(self, func, *args, **kwargs):
        """Выполнить синхронную функцию и захватить её вывод."""
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            self._log(f"Запуск: {func.__name__}")
            result = func(*args, **kwargs)
            output = sys.stdout.getvalue()
            if output:
                self._log(output)
            self._log(f"Готово: {result if result is not None else 'OK'}")
        except Exception as e:
            self._log(f"Ошибка: {e}")
            QMessageBox.critical(self, "Ошибка", str(e))
        finally:
            sys.stdout = old_stdout

    def _run_async(self, coro, *args, **kwargs):
        """Запустить асинхронную корутину в отдельном потоке."""
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # indeterminate
        self._log(f"Запуск асинхронной задачи...")

        worker = AsyncWorker(coro, *args, **kwargs)
        worker.log.connect(self._log)
        worker.finished.connect(self._on_async_finished)
        worker.start()

    def _on_async_finished(self, success, message):
        self.progress.setVisible(False)
        if success:
            self._log(f"✅ {message}")
        else:
            self._log(f"❌ Ошибка: {message}")
            QMessageBox.warning(self, "Ошибка", message)

    # --- Обработчики кнопок ---

    def _run_check(self):
        lang = self._get_lang()
        self._run_sync(check_translation, lang, json_output=False)

    def _run_stats(self):
        self._run_sync(show_all_stats, json_output=False)

    def _run_validate(self):
        lang = self._get_lang()
        dry = self.dry_run_check.isChecked()
        self._run_sync(validate_translation, lang, dry_run=dry)

    def _run_update(self):
        lang = self._get_lang()
        self._run_sync(run_lupdate, lang)

    def _run_compile(self):
        lang = self._get_lang()
        self._run_sync(run_lrelease, lang)

    def _run_auto_translate(self):
        lang = self._get_lang()
        service = self.service_combo.currentText()
        kwargs = self._get_service_kwargs()
        config = self._get_config()
        set_translation_config(config)
        dry = self.dry_run_check.isChecked()
        self._run_async(auto_translate_file, lang, service, True, dry_run=dry, **kwargs)

    def _run_process(self):
        lang = self._get_lang()
        service = self.service_combo.currentText()
        kwargs = self._get_service_kwargs()
        config = self._get_config()
        set_translation_config(config)
        dry = self.dry_run_check.isChecked()
        self._run_async(process_language, lang, service, True, dry_run=dry, **kwargs)

    def _add_new_language(self):
        """Диалог для создания нового языка."""
        from PySide6.QtWidgets import QInputDialog
        code, ok = QInputDialog.getText(self, "Новый язык", "Введите код языка (например, fr_FR):")
        if ok and code:
            # Создаём пустой .ts файл
            target = Path("locales") / f"{code}.ts"
            if target.exists():
                QMessageBox.warning(self, "Уже существует", f"Файл {target} уже существует.")
                return
            # Создаём базовый .ts
            try:
                from translation_helper import run_lupdate
                # Сначала нужно создать пустой файл, lupdate сам заполнит.
                target.parent.mkdir(exist_ok=True)
                target.touch()
                # Запускаем lupdate для этого файла
                run_lupdate(code)
                self._refresh_langs()
                self.lang_combo.setCurrentText(code)
                self._log(f"Создан язык {code}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TranslationGUI()
    window.show()
    sys.exit(app.exec())