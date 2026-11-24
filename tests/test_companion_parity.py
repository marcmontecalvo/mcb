"""Every combat action is callable the same way from /mcb, the control API, MCP and the
chat model, and the operator-only trial op stays off all of them (the parity rule in
COMPANION_COMBAT_PLAN_2026-11-23.md)."""

import re
from pathlib import Path

from test_companion_ingress import BOT, FakeHost, FakeTalker, Source

from mclab import companion_intents as intents
from mclab import mcb_mcp
from mclab.companion_ingress import Ingress
from mclab.companion_intents import INTENTS, JOB_INTENTS, UNTRUSTED_INTENTS, Actor

MCB_COMMAND = (
    Path(__file__).parents[1]
    / "mcb-body/src/main/java/com/vulpine/mcb/body/McbCommand.java"
)
COMBAT_ACTIONS = ("attack", "defense", "gather")  # hunting is gather of a drop


def mcb_subcommands() -> set[str]:
    """The sub names /mcb sends to Python (``run(.., "sub", ..)``, ``send("sub")``)."""
    source = MCB_COMMAND.read_text(encoding="utf-8")
    return set(re.findall(r'run\(context\.getSource\(\), "(\w+)"', source)) | set(
        re.findall(r'\bsend\("(\w+)"\)', source)
    )


def test_every_intent_has_an_mcp_tool_with_the_same_schema():
    tools = {t["name"]: t for t in mcb_mcp._tools()}
    assert set(INTENTS) <= set(tools)
    for name, (_, properties, required) in INTENTS.items():
        schema = tools[name]["inputSchema"]
        # the MCP server adds an idempotency key to every job tool
        extra = {"key"} if name in JOB_INTENTS else set()
        assert set(schema["properties"]) == set(properties) | extra
        assert set(schema.get("required", ())) == set(required)
        assert tools[name]["description"]


def test_a_trusted_chat_model_gets_every_intent_a_stranger_only_the_safe_ones():
    assert {t["name"] for t in intents.tool_schemas(trusted=True)} == set(INTENTS)
    safe = {t["name"] for t in intents.tool_schemas(trusted=False)}
    assert safe == set(UNTRUSTED_INTENTS)
    assert not set(COMBAT_ACTIONS) & safe


def test_the_combat_actions_are_intents_a_job_or_a_setting():
    assert set(COMBAT_ACTIONS) <= set(INTENTS)
    assert {"attack", "gather"} <= JOB_INTENTS and "defense" not in JOB_INTENTS
    assert "attack" not in UNTRUSTED_INTENTS and "defense" not in UNTRUSTED_INTENTS


def test_hunting_is_a_gather_of_a_drop_and_every_surface_says_so():
    description = INTENTS["gather"][0]
    assert "porkchop" in description
    tools = {t["name"]: t for t in mcb_mcp._tools()}
    assert "porkchop" in tools["gather"]["description"]


def test_the_mcp_instructions_list_attack_among_the_job_tools():
    assert "attack" in mcb_mcp.INSTRUCTIONS.split("Job tools")[1].split(")")[0]


def test_trial_is_on_no_surface():
    assert "trial" not in INTENTS
    assert "trial" not in {t["name"] for t in mcb_mcp._tools()}
    assert "trial" not in {t["name"] for t in intents.tool_schemas()}
    assert "trial" not in mcb_subcommands()
    assert "trial" not in MCB_COMMAND.read_text(encoding="utf-8").lower()


def test_every_mcb_subcommand_is_handled_by_the_ingress():
    subs = mcb_subcommands()
    wanted = {"status", "stop", "goto", "gather", "build", "ask", "attack", "defense"}
    assert wanted <= subs
    for sub in sorted(subs):
        host = FakeHost()
        ingress = Ingress(host, FakeTalker(), Source(), bot=BOT)
        ingress._command(Actor("command", "Owner", "u-1", None), {"command": sub})
        assert "Unknown command." not in [text for _, text in host.tells()], sub


def test_the_mcb_attack_command_takes_a_target_and_an_optional_weapon():
    source = MCB_COMMAND.read_text(encoding="utf-8")
    assert 'Commands.literal("attack")' in source
    assert '"target"' in source and '"weapon"' in source
    assert "suggestPlayers" in source  # tab-completion of online names


def test_the_mcb_defense_command_maps_onto_the_defense_intent_arguments():
    source = MCB_COMMAND.read_text(encoding="utf-8")
    for key in ("floor_hearts", "threat_radius"):
        assert f'"{key}"' in source
        assert key in INTENTS["defense"][1]
