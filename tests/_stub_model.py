"""A minimal strands.models.model.Model implementation that always raises,
used to exercise DomainAgent's exception -> deterministic-fallback path
without any network call or a running Ollama server. Not a real model --
just enough of the abstract interface to construct an Agent with it."""

from __future__ import annotations

from strands.models.model import Model


class FailingModel(Model):
    def get_config(self):
        return {}

    def update_config(self, **kwargs):
        pass

    async def stream(self, *args, **kwargs):
        raise RuntimeError("FailingModel: intentionally raises, no real model call")
        yield  # pragma: no cover -- makes this an async generator

    async def structured_output(self, *args, **kwargs):
        raise RuntimeError("FailingModel: intentionally raises, no real model call")
        yield  # pragma: no cover
