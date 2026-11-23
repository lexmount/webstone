from harness.web_fetch.providers.codex import parse_returns


def trace(targets):
    events, session = [], []
    for i, target in enumerate(targets):
        events.append(
            {
                "type": "item.completed",
                "item": {"type": "web_search", "results": [{"ref_id": f"view{i}"}]},
            }
        )
        call = {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": str(i),
            "input": 'const r = await tools.web__run({open:[{ref_id:"'
            + target
            + '"}],response_length:"long"}); text(r)',
        }
        output = {
            "type": "custom_tool_call_output",
            "call_id": str(i),
            "output": [{"text": f"L{i}: paragraph {i}"}],
        }
        session.extend({"type": "response_item", "payload": p} for p in (call, output))
    session.append(
        {"type": "response_item", "payload": {"type": "message", "text": "model answer"}}
    )
    return events, session


def test_single_open_only_scores_tool_text():
    result = parse_returns("url", "single", *trace(["url"]))
    assert result.ok and result.content == "paragraph 0"
    assert not parse_returns("url", "single", *trace(["url", "view0"])).content


def test_continuation_accepts_only_this_pages_references():
    result = parse_returns("url", "same-page", *trace(["url", "view0"]))
    assert result.ok and result.content == "paragraph 0\nparagraph 1"
    assert not parse_returns("url", "same-page", *trace(["url", "elsewhere"])).ok


def test_codex_modes_have_report_interfaces():
    from harness.web_fetch.methods import method_page_model
    from harness.web_fetch.report_interfaces import method_spec

    for provider in ("codex-single", "codex-same-page"):
        row = {"provider": provider, "provider_type": "codex_cli"}
        for language in ("en", "zh"):
            model = method_page_model(provider, provider, [row], language=language)
            assert model["endpoint"] == "codex exec"
            assert (
                method_spec(provider, row, language=language)["request"]["url"]
                == "https://example.com/"
            )
