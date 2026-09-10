from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont, QBrush
from PySide6.QtCore import Qt, QRect

app = QGuiApplication([])
img = QImage(256, 256, QImage.Format_ARGB32)
img.fill(QColor(10, 10, 10, 0))  # Transparent outer background

p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setRenderHint(QPainter.TextAntialiasing)

# Blue Lab rounded electric blue badge
p.setBrush(QBrush(QColor(23, 73, 232)))
p.setPen(Qt.NoPen)
p.drawRoundedRect(12, 12, 232, 232, 54, 54)

# White smiley :)
p.setPen(QColor(255, 255, 255))
font = QFont("Segoe UI", 120, QFont.Bold)
p.setFont(font)
p.drawText(QRect(0, 0, 256, 246), Qt.AlignCenter, ":)")
p.end()

img.save("icon.png")
img.save("icon.ico")
print("Happiptv icon created successfully!")
