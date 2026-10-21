
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QColor, QPixmap
from PyQt6.QtWidgets import QLabel


class VisionPreview(QLabel):
    focusChanged = pyqtSignal(object)
    selectionFailed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__('Inicie a visão para abrir a câmera.', parent)
        self._frame = None
        self._focus = None
        self._drag_start = None
        self._drag_end = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip('Arraste sobre a imagem para marcar um detalhe. A área é analisada junto da cena geral.')
        self.setAccessibleName('Prévia da câmera com seleção de área')

    @property
    def focus(self):
        return self._focus

    def clear_focus(self):
        self._focus = None
        self._drag_start = self._drag_end = None
        self.focusChanged.emit(None)
        self.update()

    def clear(self):
        self._frame = None
        self._drag_start = self._drag_end = None
        super().clear()

    def set_frame(self, jpeg):
        frame = QPixmap()
        if not frame.loadFromData(jpeg, 'JPEG'):
            self.clear()
            self.setText('Quadro inválido')
            return
        self._frame = frame
        self.update()

    def image_rect(self):
        if self._frame is None:
            return QRectF()
        area = QRectF(self.contentsRect()).adjusted(2, 2, -2, -2)
        size = self._frame.size()
        size.scale(area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(area.center().x()-size.width()/2, area.center().y()-size.height()/2,
                      size.width(), size.height())

    def _bounded(self, point):
        area = self.image_rect()
        return QPointF(min(area.right(), max(area.left(), point.x())),
                       min(area.bottom(), max(area.top(), point.y())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self._drag_start = self._drag_end = self._bounded(event.position())
            self.update()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self._drag_end = self._bounded(event.position())
            self.update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._drag_start is None:
            super().mouseReleaseEvent(event)
            return
        area = self.image_rect()
        selection = QRectF(self._drag_start, self._bounded(event.position())).normalized()
        self._drag_start = self._drag_end = None
        if area.isEmpty():
            return
        if (selection.width()/area.width()*self._frame.width() < 16 or
                selection.height()/area.height()*self._frame.height() < 16):
            self.selectionFailed.emit('Marque uma área maior para analisar os detalhes.')
            self.update()
            return
        self._focus = ((selection.left()-area.left())/area.width(),
                       (selection.top()-area.top())/area.height(),
                       (selection.right()-area.left())/area.width(),
                       (selection.bottom()-area.top())/area.height())
        self.focusChanged.emit(self._focus)
        self.update()

    def paintEvent(self, event):
        if self._frame is None:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#07151a'))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = self.image_rect()
        painter.drawPixmap(area, self._frame, QRectF(self._frame.rect()))
        selection = None
        if self._drag_start is not None:
            selection = QRectF(self._drag_start, self._drag_end).normalized()
        elif self._focus is not None:
            left, top, right, bottom = self._focus
            selection = QRectF(area.left()+left*area.width(), area.top()+top*area.height(),
                               (right-left)*area.width(), (bottom-top)*area.height())
        if selection is not None:
            painter.setPen(QPen(QColor('#65e8d8'), 2))
            painter.drawRect(selection)
            painter.fillRect(selection, QColor(101, 232, 216, 24))
            if selection.width() >= 110 and selection.height() >= 32:
                tag = QRectF(selection.left()+4, selection.top()+4, 104, 24)
                painter.fillRect(tag, QColor('#07151a'))
                painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, 'Área marcada')
        painter.end()
