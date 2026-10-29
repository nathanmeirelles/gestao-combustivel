"""scripts/monitor.sh: log, código de saída, trava contra execução sobreposta e rotação do log."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "monitor.sh"
RAIZ = SCRIPT.parent.parent


def rodar(dados, **extra):
    env = {**os.environ, "PRECOS_DATA_DIR": str(dados), "PYTHONPATH": str(RAIZ), **extra}
    return subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60, check=False)


@unittest.skipUnless(shutil.which("sh"), "precisa de sh")
class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.dados = Path(tempfile.mkdtemp())

    def test_sem_monitores_sai_com_zero_e_registra_no_log(self):
        r = rodar(self.dados)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = (self.dados / "monitor.log").read_text()
        self.assertIn("monitor check ===", log)
        self.assertNotIn("[erro]", log)

    def test_erro_do_python_vira_codigo_diferente_de_zero_e_fica_no_log(self):
        r = rodar(self.dados, PRECOS_PYTHON="/caminho/que/nao/existe")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[erro]", (self.dados / "monitor.log").read_text())

    def test_log_grande_e_rotacionado(self):
        (self.dados / "monitor.log").write_text("x" * 1_200_000)
        rodar(self.dados)
        self.assertLess((self.dados / "monitor.log").stat().st_size, 400_000)

    @unittest.skipUnless(shutil.which("flock"), "precisa de flock")
    def test_execucao_sobreposta_e_pulada_com_codigo_75(self):
        trava = subprocess.Popen(["flock", str(self.dados / "monitor.lock"), "sleep", "5"])
        try:
            import time
            time.sleep(0.5)
            r = rodar(self.dados)
            self.assertEqual(r.returncode, 75)
            self.assertIn("outra execução ainda está em andamento", (self.dados / "monitor.log").read_text())
        finally:
            trava.kill()
            trava.wait()

    def test_o_script_e_executavel(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))


if __name__ == "__main__":
    unittest.main()
