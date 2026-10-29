"""Impresora simulada para las pruebas: imita win32print y guarda los bytes recibidos."""

CAJON_PIN2 = b'\x1b\x70\x00\x19\xfa'
CAJON_PIN5 = b'\x1b\x70\x01\x19\xfa'
CORTE = b'\x1d\x56\x42\x00'


class ImpresoraSimulada:
    """Imita la parte de win32print que usa el TPV."""
    PRINTER_ENUM_LOCAL = 2
    PRINTER_ENUM_CONNECTIONS = 4

    def __init__(self):
        self.trabajos = []
        self.impresoras = {'TM-T20': {'Status': 0, 'Attributes': 0, 'cJobs': 0}}
        self.fallar = False

    def OpenPrinter(self, nombre):
        if nombre not in self.impresoras:
            raise Exception(f'La impresora {nombre} no existe')
        return nombre

    def StartDocPrinter(self, handle, nivel, info):
        if self.fallar:
            raise Exception('Error de impresion simulado')
        self._actual = {'impresora': handle, 'nombre': info[0], 'tipo': info[2], 'datos': b''}

    def StartPagePrinter(self, handle):
        pass

    def WritePrinter(self, handle, datos):
        self._actual['datos'] += datos

    def EndPagePrinter(self, handle):
        pass

    def EndDocPrinter(self, handle):
        self.trabajos.append(self._actual)

    def ClosePrinter(self, handle):
        pass

    def GetPrinter(self, handle, nivel):
        return dict(self.impresoras[handle])

    def EnumPrinters(self, flags):
        return [(0, '', nombre, '') for nombre in self.impresoras]


def texto_impreso(datos):
    """Quita los comandos ESC/POS y devuelve las lineas de texto del ticket."""
    longitud = {0x40: 1, 0x74: 2, 0x61: 2, 0x45: 2, 0x64: 2, 0x70: 4}  # bytes tras ESC
    salida = bytearray()
    i = 0
    while i < len(datos):
        b = datos[i]
        if b == 0x1B:
            i += 1 + longitud[datos[i + 1]]
        elif b == 0x1D:
            i += 3 if datos[i + 1] == 0x21 else 4   # GS ! n  /  GS V 66 n
        else:
            salida.append(b)
            i += 1
    return salida.decode('cp858').split('\n')
