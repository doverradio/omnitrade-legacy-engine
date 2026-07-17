__all__ = ["run_forever"]


def __getattr__(name: str):
    if name == "run_forever":
        from app.services.orchestration.continuous_pipeline_worker import run_forever

        return run_forever
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
