import sys
import base64
import re
import time
import io
from enum import Enum

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTextEdit, QLabel, QPushButton, 
                             QFileDialog, QProgressBar, QFrame, QMessageBox,
                             QComboBox, QScrollArea, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QMimeData
from PyQt6.QtGui import QAction, QIcon, QPixmap, QImage, QDragEnterEvent, QDropEvent, QColor, QPalette

from PIL import Image

# ==========================================
# 🎨 视觉美学 (Dark Theme / Glassmorphism 模拟)
# ==========================================
STYLESHEET = """
QMainWindow {
    background-color: #0a0a0f;
}
QWidget {
    color: #f1f5f9;
    font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
    font-size: 14px;
}
QFrame#Panel {
    background-color: #12121a;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
}
QTextEdit {
    background-color: rgba(18, 18, 26, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    color: #a5b4fc;
    padding: 10px;
    selection-background-color: #6366f1;
}
QPushButton {
    background-color: #4f46e5;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #6366f1;
}
QPushButton:pressed {
    background-color: #4338ca;
}
QPushButton#SecondaryBtn {
    background-color: #1e1e2e;
    border: 1px solid rgba(255,255,255,0.1);
}
QPushButton#SecondaryBtn:hover {
    border: 1px solid #6366f1;
}
QLabel#Title {
    font-size: 24px;
    font-weight: bold;
    color: #818cf8;
}
QLabel#Subtitle {
    color: #94a3b8;
    font-size: 12px;
}
QProgressBar {
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 4px;
    text-align: center;
    background-color: #1a1a24;
}
QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #22d3ee);
    border-radius: 4px;
}
QLabel#DropZone {
    border: 2px dashed #4f46e5;
    border-radius: 10px;
    background-color: rgba(79, 70, 229, 0.05);
    color: #818cf8;
    font-weight: bold;
}
QLabel#DropZone:hover {
    background-color: rgba(79, 70, 229, 0.1);
}
"""

# ==========================================
# 🧠 核心逻辑：后台工作线程 (QThread)
# ==========================================

class ConversionMode(Enum):
    BASE64_TO_IMAGE = 1
    IMAGE_TO_BASE64 = 2

class WorkerSignals(QThread):
    """定义信号，用于线程间通信"""
    progress = pyqtSignal(int, str)       # 进度百分比, 描述文本
    finished_b2i = pyqtSignal(object, dict) # 成功转图: (PIL.Image, 性能数据)
    finished_i2b = pyqtSignal(str, dict)    # 成功转码: (Base64Str, 性能数据)
    error = pyqtSignal(str)               # 错误信息

class ImageProcessor(WorkerSignals):
    def __init__(self, mode, data=None):
        super().__init__()
        self.mode = mode
        self.data = data # 可以是文件路径(str) 或 Base64字符串(str)
        self.is_running = True

    def run(self):
        start_time = time.perf_counter()
        try:
            if self.mode == ConversionMode.BASE64_TO_IMAGE:
                self._process_b2i(start_time)
            else:
                self._process_i2b(start_time)
        except Exception as e:
            self.error.emit(f"处理异常: {str(e)}")

    def _process_b2i(self, start_time):
        """Base64 -> Image 核心逻辑"""
        raw_text = self.data
        self.progress.emit(10, "正在解析 Base64 格式...")
        
        # 1. 智能正则提取 (复刻原 JS 逻辑)
        # 匹配 data:image/xxx;base64, 后的内容
        pattern = re.compile(r'data:image\/([a-zA-Z0-9+.-]+);base64,([A-Za-z0-9+/=]+)')
        match = pattern.search(raw_text)
        
        if match:
            b64_data = match.group(2)
            mime_type = match.group(1)
        else:
            # 尝试直接清洗
            b64_data = re.sub(r'[\s\r\n]', '', raw_text)
            # 简单校验
            if len(b64_data) % 4 != 0 or not re.match(r'^[A-Za-z0-9+/=]+$', b64_data):
                # 尝试补全 padding
                missing_padding = len(b64_data) % 4
                if missing_padding:
                    b64_data += '=' * (4 - missing_padding)
            mime_type = "unknown"

        self.progress.emit(30, "正在解码二进制数据...")
        try:
            image_data = base64.b64decode(b64_data)
        except Exception:
            raise ValueError("无效的 Base64 字符串")

        self.progress.emit(60, "正在构建图像对象...")
        # 使用 BytesIO 在内存中操作，实现无损
        img_buffer = io.BytesIO(image_data)
        try:
            image = Image.open(img_buffer)
            image.load() # 强制加载到内存
        except Exception:
            raise ValueError("无法识别的图像数据")

        end_time = time.perf_counter()
        duration = end_time - start_time
        size_bytes = len(image_data)
        
        perf_data = {
            "time": duration,
            "size": size_bytes,
            "format": image.format,
            "mode": image.mode,
            "width": image.width,
            "height": image.height
        }
        
        self.progress.emit(100, "转换完成")
        self.finished_b2i.emit(image, perf_data)

    def _process_i2b(self, start_time):
        """Image -> Base64 核心逻辑"""
        file_path = self.data
        self.progress.emit(10, "正在读取文件...")
        
        with open(file_path, "rb") as f:
            file_data = f.read()
            
        self.progress.emit(40, "正在进行 Base64 编码...")
        b64_bytes = base64.b64encode(file_data)
        b64_str = b64_bytes.decode('utf-8')
        
        self.progress.emit(80, "正在生成 Data URI...")
        # 简单的 MIME 推断
        ext = file_path.split('.')[-1].lower()
        mime_map = {
            'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 
            'gif': 'gif', 'webp': 'webp', 'bmp': 'bmp', 
            'ico': 'x-icon', 'svg': 'svg+xml'
        }
        mime = mime_map.get(ext, 'octet-stream')
        result = f"data:image/{mime};base64,{b64_str}"
        
        end_time = time.perf_counter()
        perf_data = {
            "time": end_time - start_time,
            "size": len(file_data)
        }
        
        self.progress.emit(100, "编码完成")
        self.finished_i2b.emit(result, perf_data)

# ==========================================
# 🖥️ UI 组件：支持拖拽的 Label
# ==========================================
class DragDropLabel(QLabel):
    fileDropped = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("\n📂\n\n拖拽图像文件到此处\n或点击选择文件")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
            self.setStyleSheet("background-color: rgba(79, 70, 229, 0.2); border-color: #6366f1;")
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("") # 恢复默认样式

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet("")
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files:
            self.fileDropped.emit(files[0])

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            file_path, _ = QFileDialog.getOpenFileName(self, "选择图像", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.ico *.svg *.gif)")
            if file_path:
                self.fileDropped.emit(file_path)

# ==========================================
# 📱 主窗口
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Base64 图像引擎 (Zenith Python版)")
        self.resize(1200, 800)
        self.current_image = None # 存储 PIL Image 对象
        self.init_ui()
        
        # 应用样式
        app = QApplication.instance()
        app.setStyleSheet(STYLESHEET)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(24, 24, 24, 24)

        # --- 1. 头部 ---
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Base64 Image Engine")
        title.setObjectName("Title")
        subtitle = QLabel("高性能无损图像处理引擎 | Python Native")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        
        # 性能看板
        self.perf_label = QLabel("🚀 等待处理...")
        self.perf_label.setStyleSheet("color: #10b981; font-weight: bold; background: rgba(16, 185, 129, 0.1); padding: 8px 16px; border-radius: 6px;")
        
        header.addLayout(title_box)
        header.addStretch()
        header.addWidget(self.perf_label)
        main_layout.addLayout(header)

        # --- 2. 主体内容 (左右分栏) ---
        content_layout = QHBoxLayout()
        
        # 左侧：输入区
        left_panel = QFrame()
        left_panel.setObjectName("Panel")
        left_layout = QVBoxLayout(left_panel)
        
        # 模式切换
        mode_layout = QHBoxLayout()
        self.btn_b2i = QPushButton("Base64 → 图像")
        self.btn_i2b = QPushButton("图像 → Base64")
        self.btn_i2b.setObjectName("SecondaryBtn")
        self.btn_b2i.clicked.connect(lambda: self.switch_mode(ConversionMode.BASE64_TO_IMAGE))
        self.btn_i2b.clicked.connect(lambda: self.switch_mode(ConversionMode.IMAGE_TO_BASE64))
        mode_layout.addWidget(self.btn_b2i)
        mode_layout.addWidget(self.btn_i2b)
        left_layout.addLayout(mode_layout)

        # 堆叠控件：根据模式显示不同输入
        self.input_stack = QVBoxLayout()
        
        # B2I 输入控件
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("在此粘贴 Base64 代码...")
        
        # I2B 输入控件
        self.drop_zone = DragDropLabel()
        self.drop_zone.fileDropped.connect(self.start_i2b_conversion)
        self.drop_zone.hide() # 默认隐藏

        left_layout.addWidget(self.text_input)
        left_layout.addWidget(self.drop_zone)
        
        # 操作按钮
        action_layout = QHBoxLayout()
        self.btn_convert = QPushButton("开始转换")
        self.btn_convert.clicked.connect(self.start_conversion)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("SecondaryBtn")
        self.btn_clear.clicked.connect(self.clear_all)
        
        action_layout.addWidget(self.btn_convert)
        action_layout.addWidget(self.btn_clear)
        left_layout.addLayout(action_layout)

        # 右侧：预览区
        right_panel = QFrame()
        right_panel.setObjectName("Panel")
        right_layout = QVBoxLayout(right_panel)
        
        self.preview_label = QLabel("预览区域")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("color: #64748b; font-size: 16px;")
        
        # 滚动区域包裹预览图，防止大图撑破界面
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.preview_label)
        scroll_area.setStyleSheet("background: transparent; border: none;")
        
        right_layout.addWidget(scroll_area)
        
        # 底部信息栏
        self.info_label = QLabel("-")
        self.info_label.setStyleSheet("color: #94a3b8;")
        right_layout.addWidget(self.info_label)
        
        # 保存按钮
        self.btn_save = QPushButton("保存图像")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_image)
        right_layout.addWidget(self.btn_save)

        # 设置左右比例
        content_layout.addWidget(left_panel, 2)
        content_layout.addWidget(right_panel, 3)
        main_layout.addLayout(content_layout)

        # --- 3. 底部进度条 ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        self.current_mode = ConversionMode.BASE64_TO_IMAGE

    def switch_mode(self, mode):
        self.current_mode = mode
        if mode == ConversionMode.BASE64_TO_IMAGE:
            self.btn_b2i.setObjectName("")
            self.btn_i2b.setObjectName("SecondaryBtn")
            self.text_input.show()
            self.drop_zone.hide()
            self.btn_convert.setText("解析并预览")
            self.btn_convert.show()
        else:
            self.btn_b2i.setObjectName("SecondaryBtn")
            self.btn_i2b.setObjectName("")
            self.text_input.hide()
            self.drop_zone.show()
            self.btn_convert.hide() # 拖拽即自动开始，隐藏按钮
        
        # 刷新样式
        self.btn_b2i.style().unpolish(self.btn_b2i)
        self.btn_b2i.style().polish(self.btn_b2i)
        self.btn_i2b.style().unpolish(self.btn_i2b)
        self.btn_i2b.style().polish(self.btn_i2b)

    def start_conversion(self):
        if self.current_mode == ConversionMode.BASE64_TO_IMAGE:
            text = self.text_input.toPlainText().strip()
            if not text:
                QMessageBox.warning(self, "提示", "请输入 Base64 内容")
                return
            self.run_worker(ConversionMode.BASE64_TO_IMAGE, text)

    def start_i2b_conversion(self, file_path):
        self.run_worker(ConversionMode.IMAGE_TO_BASE64, file_path)

    def run_worker(self, mode, data):
        # UI 状态更新
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.btn_convert.setEnabled(False)
        self.drop_zone.setEnabled(False)
        
        # 启动线程
        self.worker = ImageProcessor(mode, data)
        self.worker.progress.connect(self.update_progress)
        self.worker.error.connect(self.handle_error)
        self.worker.finished_b2i.connect(self.handle_b2i_success)
        self.worker.finished_i2b.connect(self.handle_i2b_success)
        self.worker.start()

    def update_progress(self, val, text):
        self.progress_bar.setValue(val)
        self.progress_bar.setFormat(f"{text} %p%")

    def handle_error(self, msg):
        self.reset_ui_state()
        QMessageBox.critical(self, "错误", msg)

    def handle_b2i_success(self, pil_image, perf):
        self.reset_ui_state()
        self.current_image = pil_image
        
        # 更新性能看板
        speed = (perf['size'] / 1024 / 1024) / perf['time']
        self.perf_label.setText(f"⏱️ {perf['time']:.3f}s | 📦 {perf['size']/1024:.1f}KB | ⚡ {speed:.1f} MB/s")
        
        # 更新信息
        self.info_label.setText(f"尺寸: {perf['width']}x{perf['height']} | 格式: {perf['format']} | 模式: {perf['mode']}")
        
        # 显示预览 (转换 PIL Image 到 QPixmap)
        # 注意：为了性能，预览图可以缩小，但保存时是原图
        im_data = self.current_image.convert("RGBA").tobytes("raw", "RGBA")
        qim = QImage(im_data, self.current_image.width, self.current_image.height, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qim)
        
        # 缩放以适应窗口
        if pixmap.width() > self.preview_label.width():
            pixmap = pixmap.scaled(self.preview_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            
        self.preview_label.setPixmap(pixmap)
        self.btn_save.setEnabled(True)
        self.btn_save.setText("保存图像 (无损)")

    def handle_i2b_success(self, b64_str, perf):
        self.reset_ui_state()
        
        # 自动切换回文本模式显示结果
        self.switch_mode(ConversionMode.BASE64_TO_IMAGE)
        self.text_input.setPlainText(b64_str)
        
        speed = (perf['size'] / 1024 / 1024) / perf['time']
        self.perf_label.setText(f"⏱️ {perf['time']:.3f}s | ⚡ {speed:.1f} MB/s")
        QMessageBox.information(self, "成功", "Base64 编码已生成并复制到输入框！")

    def reset_ui_state(self):
        self.progress_bar.hide()
        self.btn_convert.setEnabled(True)
        self.drop_zone.setEnabled(True)

    def clear_all(self):
        self.text_input.clear()
        self.preview_label.clear()
        self.preview_label.setText("预览区域")
        self.info_label.setText("-")
        self.perf_label.setText("🚀 等待处理...")
        self.current_image = None
        self.btn_save.setEnabled(False)

    def save_image(self):
        if not self.current_image:
            return
            
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存图像", f"export_{int(time.time())}.png", 
            "PNG (*.png);;JPEG (*.jpg);;WebP (*.webp);;ICO (*.ico);;BMP (*.bmp)"
        )
        
        if file_path:
            try:
                # 使用 PIL 保存，保证无损和格式控制
                self.current_image.save(file_path)
                QMessageBox.information(self, "成功", f"图像已保存至: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
