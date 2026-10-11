"""阶段 7 审查 P0/P1 修复的回归测试（scripts 层与文档一致性）。

覆盖审查指出的缺口：
1. check_base_fingerprint 空目录/缺源文件不得静默通过（退出码 2 = 显式跳过，
   数量异常 = 1），防"算例数 0 也算 PASS"的假阳性；
2. solve_cli --backend 真实透传到 pipeline（结果 metrics.backend 可核验）；
3. review_check 在 --skip-copt 模式下仍跑 rebuild 默认 OR-Tools 冒烟，
   COPT 冒烟显式带 --backend copt 并校验后端；exhaustive_check 显式
   backend="copt"，杜绝"命名 copt 实为 ortools"；
4. 文档与源码/数据一致：矩阵 0 值规则（models.py 为准）、insert 不搬动
   旧任务、数据来源按 Solomon/Kilby 分族、代码块内无 :: 行内注释。
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 1. 指纹检查缺源防假通过 ----------

class TestFingerprintMissingSource:
    def test_empty_raw_dir_returns_skip_code(self, tmp_path, monkeypatch, capsys):
        fp = _load_script("check_base_fingerprint")
        monkeypatch.setattr(fp, "RAW_DIR", tmp_path)
        monkeypatch.setattr(fp, "OUT", tmp_path / "fp.json")
        rc = fp.main()
        assert rc == 2  # 显式跳过，绝不返回 0 冒充通过
        assert "SKIPPED_NO_SOURCE" in capsys.readouterr().out

    def test_partial_files_also_skip(self, tmp_path, monkeypatch, capsys):
        fp = _load_script("check_base_fingerprint")
        # 只放 10/11 个源文件：仍属缺源，不得进入"算例数 10"的正常路径
        for f in fp.EXPECTED_FILES[:-1]:
            (tmp_path / f).write_text("", encoding="utf-8")
        monkeypatch.setattr(fp, "RAW_DIR", tmp_path)
        monkeypatch.setattr(fp, "OUT", tmp_path / "fp.json")
        assert fp.main() == 2
        assert fp.EXPECTED_FILES[-1] in capsys.readouterr().out

    def test_expected_files_match_draft_bases(self):
        # 清单必须与评测实际使用的 Solomon 基础算例一一对应（防清单漂移）
        fp = _load_script("check_base_fingerprint")
        bases = set()
        for split in ("dev", "test"):
            d = ROOT / "data" / "processed" / "scenarios" / split
            for p in d.glob("*.json"):
                if p.name.startswith("kilby"):
                    continue
                bases.add(p.name.split("-s")[0].upper() + ".txt")
        assert set(fp.EXPECTED_FILES) == bases

    def _tmp_raw(self, tmp_path):
        """把 11 个真实源文件复制到临时目录（不动开发机原件）。"""
        src = ROOT / "data" / "raw" / "solomon"
        dst = tmp_path / "raw"
        dst.mkdir()
        for f in src.glob("*.txt"):
            (dst / f.name).write_bytes(f.read_bytes())
        return dst

    def test_valid_sources_pass_with_manifest(self, tmp_path, monkeypatch, capsys):
        fp = _load_script("check_base_fingerprint")
        monkeypatch.setattr(fp, "RAW_DIR", self._tmp_raw(tmp_path))
        monkeypatch.setattr(fp, "OUT", tmp_path / "fp.json")
        assert fp.main() == 0
        out = capsys.readouterr().out
        assert "manifest 一致性 11/11" in out

    def test_tampered_timewindow_fails(self, tmp_path, monkeypatch, capsys):
        # 篡改时间窗 → 字节 sha256 与 manifest 不符 → fail(1)
        fp = _load_script("check_base_fingerprint")
        raw = self._tmp_raw(tmp_path)
        victim = raw / "C101.txt"
        text = victim.read_text(encoding="utf-8")
        lines = text.splitlines()
        # 找一个 READY TIME 行改一个数字（保持行结构）
        for i, ln in enumerate(lines):
            parts = ln.split()
            if len(parts) >= 7:  # 客户行：CUST NO X Y DEMAND READY DUE SERVICE
                try:
                    float(parts[0])
                except ValueError:
                    continue  # 表头行（CUST NO. / READY TIME 等）
                parts[4] = str(float(parts[4]) + 1.0)  # READY TIME 列
                lines[i] = "  ".join(parts)
                break
        victim.write_text("\n".join(lines) + "\n", encoding="utf-8")
        monkeypatch.setattr(fp, "RAW_DIR", raw)
        monkeypatch.setattr(fp, "OUT", tmp_path / "fp.json")
        assert fp.main() == 1
        assert "manifest" in capsys.readouterr().out

    def test_replaced_content_same_filename_fails(self, tmp_path, monkeypatch, capsys):
        # 保留文件名、替换文件内容 → fail(1)
        fp = _load_script("check_base_fingerprint")
        raw = self._tmp_raw(tmp_path)
        (raw / "R101.txt").write_bytes((raw / "C101.txt").read_bytes())
        monkeypatch.setattr(fp, "RAW_DIR", raw)
        monkeypatch.setattr(fp, "OUT", tmp_path / "fp.json")
        assert fp.main() == 1
        assert "R101.txt" in capsys.readouterr().out

    def test_missing_manifest_fails(self, tmp_path, monkeypatch):
        fp = _load_script("check_base_fingerprint")
        monkeypatch.setattr(fp, "RAW_DIR", self._tmp_raw(tmp_path))
        monkeypatch.setattr(fp, "MANIFEST", tmp_path / "no_such_manifest.json")
        monkeypatch.setattr(fp, "OUT", tmp_path / "fp.json")
        assert fp.main() == 1


# ---------- 2. solve_cli --backend 透传 ----------

class TestSolveCliBackend:
    def test_backend_ortools_recorded_in_result(self, tmp_path):
        out = tmp_path / "r.json"
        p = subprocess.run(
            [sys.executable, "scripts/solve_cli.py",
             "--input", "data/sample/sample_small.json",
             "--method", "rebuild", "--backend", "ortools",
             "--time-limit-ms", "5000", "--out", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        assert p.returncode == 0, p.stderr[-500:]
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["metrics"]["backend"] == "ortools"
        assert data["metrics"]["backend_requested"] == "ortools"

    def test_cli_rejects_unknown_backend(self, tmp_path):
        p = subprocess.run(
            [sys.executable, "scripts/solve_cli.py",
             "--input", "data/sample/sample_small.json",
             "--method", "rebuild", "--backend", "gurobi"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert p.returncode == 2  # argparse 非法 choices


# ---------- 3. review_check / exhaustive_check 结构防回归 ----------

class TestReviewScriptStructure:
    def test_skip_copt_mode_still_runs_default_rebuild_smoke(self):
        src = (ROOT / "scripts" / "review_check.py").read_text(encoding="utf-8")
        # rebuild_ortools 冒烟必须在 if not args.skip_copt 之前定义
        i_ortools = src.index('smoke["rebuild_ortools"]')
        i_gate = src.index("if not args.skip_copt")
        assert i_ortools < i_gate, "--skip-copt 模式也必须验证 rebuild 默认后端"
        # COPT 冒烟显式带 --backend copt
        i_copt = src.index('smoke["rebuild_copt"]')
        assert i_gate < i_copt
        seg = src[i_copt:src.index("summary[\"steps\"][\"smoke\"]")]
        assert '"--backend", "copt"' in seg
        # 两种模式都校验实际后端，不允许只看退出码
        assert src.count("assert_backend(") >= 3

    def test_exhaustive_check_explicit_copt_backend(self):
        src = (ROOT / "scripts" / "exhaustive_check.py").read_text(encoding="utf-8")
        assert 'backend="copt"' in src, "穷举核对声称验证 COPT 就必须显式指定后端"
        assert "backend_verified" in src


# ---------- 5. 后端断言严格性（二次复审 P0 反例） ----------

class TestBackendAssertionStrict:
    """审查复现的反例：backend=ortools + requested=copt + 版本含 copt，
    宽松 OR 判定会误判通过；严格判定必须失败且保留结果 JSON。"""

    def _rc(self, tmp_path, data):
        rc_mod = _load_script("review_check")
        f = tmp_path / "result.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        out = rc_mod.assert_backend(f, data["_expected"])
        return out, f

    def test_mixed_copt_named_but_ortools_actual_fails(self, tmp_path):
        out, f = self._rc(tmp_path, {
            "_expected": "copt",
            "metrics": {"backend": "ortools", "backend_requested": "copt"},
            "solver_version": "stage2-copt-8.0.6",
        })
        assert out["returncode"] == 1
        assert f.exists(), "失败结果 JSON 必须保留供复查"

    def test_mixed_ortools_named_but_copt_actual_fails(self, tmp_path):
        out, f = self._rc(tmp_path, {
            "_expected": "ortools",
            "metrics": {"backend": "copt", "backend_requested": "ortools"},
            "solver_version": "stage2-ortools-9.14.6206",
        })
        assert out["returncode"] == 1
        assert f.exists()

    def test_version_inconsistent_with_actual_backend_fails(self, tmp_path):
        out, f = self._rc(tmp_path, {
            "_expected": "copt",
            "metrics": {"backend": "copt", "backend_requested": "copt"},
            "solver_version": "stage2-ortools-9.14.6206",
        })
        assert out["returncode"] == 1
        assert f.exists()

    def test_real_copt_result_passes_and_cleans_up(self, tmp_path):
        out, f = self._rc(tmp_path, {
            "_expected": "copt",
            "metrics": {"backend": "copt", "backend_requested": "copt"},
            "solver_version": "stage2-copt-8.0.6",
        })
        assert out["returncode"] == 0
        assert not f.exists()

    def test_real_ortools_result_passes(self, tmp_path):
        out, f = self._rc(tmp_path, {
            "_expected": "ortools",
            "metrics": {"backend": "ortools", "backend_requested": "ortools"},
            "solver_version": "stage2-ortools-9.14.6206",
        })
        assert out["returncode"] == 0

    def test_exhaustive_check_no_lenient_or(self):
        src = (ROOT / "scripts" / "exhaustive_check.py").read_text(encoding="utf-8")
        assert 'or "copt" in' not in src
        assert 'backend") == "copt"' in src
        assert 'backend_requested") == "copt"' in src


# ---------- 4. 文档与源码/数据一致 ----------

class TestDocConsistency:
    def test_matrix_zero_rule_matches_models(self):
        models = (ROOT / "src" / "domain" / "models.py").read_text(encoding="utf-8")
        assert "不得仅凭 0 判作缺边" in models
        for doc in ("README.md", "docs/field_dictionary.md"):
            text = (ROOT / doc).read_text(encoding="utf-8")
            assert "非对角 0 视为缺边" not in text, f"{doc} 仍是旧矩阵规则"
            assert "合法" in text and "null" in text, f"{doc} 未写明 0 合法/null 缺边"

    def test_insert_docs_no_moving_old_tasks(self):
        alg = (ROOT / "docs" / "algorithms.md").read_text(encoding="utf-8")
        ins_line = next(l for l in alg.splitlines() if l.startswith("| insert"))
        assert "不搬动旧任务" in ins_line
        assert "有限搬动" not in ins_line
        arch = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        ins_row = next(l for l in arch.splitlines() if "insert.py" in l)
        assert "不搬动旧任务" in ins_row

    def test_data_provenance_split_by_family(self):
        lic = (ROOT / "docs" / "data_licenses.md").read_text(encoding="utf-8")
        assert "Solomon" in lic and "Kilby" in lic
        assert "synthetic_events=false" in lic
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "synthetic_events=false" in readme
        # 抽查草稿 _meta 与文档口径一致（Kilby 真实出现时间 / Solomon 合成）
        kilby = json.loads((ROOT / "data/processed/scenarios/dev/kilby-c100-s101.json")
                           .read_text(encoding="utf-8"))
        sol = json.loads((ROOT / "data/processed/scenarios/dev/c101-s101.json")
                         .read_text(encoding="utf-8"))
        assert kilby["_meta"]["synthetic_events"] is False
        assert sol["_meta"]["synthetic_events"] is True

    def test_no_inline_bat_comments_in_command_blocks(self):
        # :: 注释在 bat 代码块内会被当作实参；已验证文档的命令块不得包含
        for doc in ("README.md", "docs/demo_script.md", "docs/testing.md",
                    "docs/handoff.md"):
            text = (ROOT / doc).read_text(encoding="utf-8")
            bad = [l for l in text.splitlines() if ".exe" in l and "::" in l]
            assert not bad, f"{doc} 命令行仍含 :: 注释: {bad}"

    def test_demo_script_declares_verified_shell(self):
        text = (ROOT / "docs/demo_script.md").read_text(encoding="utf-8")
        assert "CMD" in text and "Git Bash" in text
        assert "实跑验证" in text
