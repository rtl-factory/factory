"""The board UART rate is one constant (wiki/src/common/rtl/uart/mas.md): the
RTL package value, the host library's BOARD_BAUD, every board declaration's
baud, nand2doom's uart.json and every host script's --baud default agree,
and every board top takes its rate from the package."""
import ast
import importlib
import json
from pathlib import Path
import re
import unittest

from src.common.scripts.uart_mem import uart_mem as um
from src.common.scripts.uart_mem import uart_mem_load
from src.nand2mario.scripts.board import cart_loader

ROOT = Path(__file__).resolve().parents[5]
PACKAGE = ROOT / "src/common/rtl/uart/n2a_uart_pkg.sv"


class BoardRateTests(unittest.TestCase):
    def test_rtl_package_is_the_host_constant(self):
        found = re.findall(r"localparam int unsigned N2A_UART_BOARD_BAUD\s*=\s*([\d_]+);",
                           PACKAGE.read_text())
        self.assertEqual([um.BOARD_BAUD], [int(value) for value in found])

    def test_every_host_default_and_declaration_is_the_board_rate(self):
        self.assertEqual(uart_mem_load.DEFAULT_BAUD, um.BOARD_BAUD)
        self.assertEqual(cart_loader.DEFAULT_BAUD, um.BOARD_BAUD)
        doom = json.loads((ROOT / "src/nand2doom/cfg/uart.json").read_text())
        self.assertEqual(doom["baudrate"], um.BOARD_BAUD)
        self.assertEqual(set(doom["board_baudrates"].values()), {um.BOARD_BAUD})
        declarations = sorted(ROOT.glob("src/*/cfg/board.json"))
        self.assertGreaterEqual(len(declarations), 2)
        for path in declarations:
            for board, fields in json.loads(path.read_text())["boards"].items():
                with self.subTest(declaration=str(path.relative_to(ROOT)), board=board):
                    self.assertEqual(fields["baud"], um.BOARD_BAUD)

    def test_every_host_baud_option_defaults_to_the_board_rate(self):
        # Why: found, not listed, so a new script's --baud default cannot slip past;
        # each default is evaluated in its module, and a numeric literal is refused
        # so the rate stays one constant.
        scripts = sorted(path for path in ROOT.glob("src/*/scripts/**/*.py")
                         if "tests" not in path.relative_to(ROOT).parts)
        scripts += sorted(path for path in ROOT.glob("tools/**/*.py")
                          if "tests" not in path.relative_to(ROOT).parts)
        found = 0
        for path in scripts:
            for node in ast.walk(ast.parse(path.read_text())):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "add_argument" and node.args
                        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--baud"):
                    continue
                found += 1
                where = f"{path.relative_to(ROOT)}:{node.lineno}"
                with self.subTest(option=where):
                    default = next((k.value for k in node.keywords if k.arg == "default"), None)
                    self.assertIsNotNone(default, "--baud has no default")
                    self.assertNotIsInstance(default, ast.Constant, "--baud default is a literal")
                    module = importlib.import_module(
                        ".".join(path.relative_to(ROOT).with_suffix("").parts))
                    self.assertEqual(eval(compile(ast.Expression(default), where, "eval"),
                                          vars(module)), um.BOARD_BAUD)
        self.assertGreaterEqual(found, 5)

    def test_board_tops_take_the_package_rate(self):
        for top in ("src/nand2mario/fpga/de10_lite/n2m_de10_lite.sv",
                    "src/nand2mario/fpga/de2_115/n2m_de2_115.sv"):
            text = " ".join((ROOT / top).read_text().split())
            with self.subTest(top=top):
                for function in ("clks_per_bit", "frac_num", "frac_den"):
                    self.assertIn(f"n2a_uart_pkg::n2a_uart_{function}( 25_000_000, "
                                  "n2a_uart_pkg::N2A_UART_BOARD_BAUD)", text)
        self.assertIn("N2D_UART_BOARD_BAUD     = n2a_uart_pkg::N2A_UART_BOARD_BAUD",
                      (ROOT / "src/nand2doom/rtl/platform/n2d_pkg.sv").read_text())
        for top in ("src/nand2doom/fpga/de10_lite/n2d_de10_lite.sv",
                    "src/nand2doom/fpga/de2_115/n2d_de2_115.sv"):
            with self.subTest(top=top):
                self.assertIn("N2D_UART_BOARD_BAUD", (ROOT / top).read_text())


if __name__ == "__main__":
    unittest.main()
