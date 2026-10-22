"""配置加载的接线测试。

对应 SDD docs/module-sdd/config.md 的验收项 1–3：`.env` 的值能到达
`ReasoningEngine`、`main.py` 在导入期调用 `load_dotenv()`、真实环境变量优先。

`main.py` 的功能（路由挂载、CORS、异常 handler、启动事件）在
`tests/test_routes.py` 中随接口一起验证，本文件只管「配置是怎么进来的」。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from dotenv import load_dotenv

from src.reasoning import ENV_API_KEY, ENV_BASE_URL, ENV_MODEL, ENV_USE_MOCK_LLM, ReasoningEngine
from src.tools import ToolCenter


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉本文件会经 `.env` 写入的变量。

    必须清：开发机的 `.env` 会因 `tests/test_routes.py` 导入 `main` 而渗进测试
    进程（`load_dotenv()` 是导入期的副作用），不清则断言值可能来自真实配置。

    `monkeypatch.delenv` 会记下改动并在用例结束后还原；`load_dotenv` 直接写
    `os.environ`，不受 monkeypatch 追踪，但还原时以 `delenv` 的记录为准，
    因此用例新增的键会被删掉、用例前已有的键会被恢复。
    """
    for name in (ENV_API_KEY, ENV_MODEL, ENV_BASE_URL, ENV_USE_MOCK_LLM):
        monkeypatch.delenv(name, raising=False)


def test_dotenv_values_reach_the_engine(tmp_path: Path, clean_env: None) -> None:
    """验收项 1：写进 `.env` 的值，构造出的引擎必须用上。

    这条路径就是用户「把 Key 粘进 .env」之后真正发生的事。
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=key-from-dotenv\n"
        "DEEPSEEK_MODEL=model-from-dotenv\n"
        "DEEPSEEK_BASE_URL=https://example.invalid/v1\n"
        "USE_MOCK_LLM=yes\n",
        encoding="utf-8",
    )

    load_dotenv(env_file)
    engine = ReasoningEngine(ToolCenter())

    assert engine._api_key == "key-from-dotenv"
    assert engine._model == "model-from-dotenv"
    assert engine._base_url == "https://example.invalid/v1"
    assert engine._mock_llm is True


def test_real_environment_wins_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """验收项 3：CI / 容器注入的环境变量压过开发机上的 `.env`。

    依赖 `load_dotenv()` 的默认「不覆盖」行为 —— 若哪天改成 `override=True`，
    本用例会失败，这正是它存在的意义。
    """
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_MODEL=model-from-file\n", encoding="utf-8")
    monkeypatch.setenv(ENV_MODEL, "model-from-environment")

    load_dotenv(env_file)

    assert ReasoningEngine(ToolCenter(), api_key="")._model == "model-from-environment"


def test_main_calls_load_dotenv_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """验收项 2：`import main` 必须触发一次 `load_dotenv()`。

    `main.py` 写的是 `from dotenv import load_dotenv`，即把函数**绑定进自己的
    命名空间**；reload 会重新执行这行导入，所以打桩点必须是 `dotenv.load_dotenv`
    （源模块属性），打 `main.load_dotenv` 会在 reload 时被覆盖掉。

    reload 后 `main.load_dotenv` 指向打桩函数，用例结束前手动还原，避免把桩
    留在模块命名空间里（monkeypatch 只管它自己改过的属性）。
    """
    import dotenv
    import main

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def spy(*args: object, **kwargs: object) -> bool:
        calls.append((args, kwargs))
        return True

    original = main.load_dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", spy)
    try:
        importlib.reload(main)
    finally:
        main.load_dotenv = original

    assert len(calls) == 1, "导入 main 应当且只应当调用一次 load_dotenv()"
    # 无参调用：让 python-dotenv 自行从 main.py 所在目录向上找 .env，
    # 这样从任意工作目录启动都能读到项目根目录的配置。
    assert calls[0] == ((), {})
