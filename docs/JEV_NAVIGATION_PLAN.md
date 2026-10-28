# Hybrid navigation: next five steps

> **Partly superseded (Oct 28, 2026):** Jev's role as a bounded local-waypoint selector (`bounded-local-waypoint-selector-v1`) is legacy. Jev now sets goals, cost profiles and tactical interrupts; deterministic code chooses routes. See [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md).

Created October 25, 2026. This is the next implementation and experiment plan,
not qualification evidence. Marc authorized continued work after completion of
the OpenJev, Laya and Qwen3.5-4B direct-scoring screens.

Marc proposed a candidate architecture: a larger LLM for planning and Jev for fast
tactical choices, threats and opportunistic pickups. He explicitly clarified that
this is a hypothesis to test, not the selected winning design. The harness now
supports optional single-model and tandem C1 configurations; role assignment is
configurable. The proposed broader contracts are in
[hybrid architecture](HYBRID_ARCHITECTURE.md). Compare alternatives and keep claims
limited to the conditions actually run.

## 1. Define the navigation decision contract

Contract prepared from the current code and technical handoff. Intended profile:
`bounded-local-waypoint-selector-v1`, separate from the existing C1 tactical and
deterministic navigation profiles. Implementation and verification are pending.

The parent design is `hybrid-planner-tactical-motor-v1`: versioned strategic
plans/subgoals, bounded Jev commitments, and a shared local motor. These remain
separate objects and separate backend instances. Slow planning cannot block
tactical admission or the native tick loop. Strategic goals constrain the local
menu; Jev can interrupt within the plan's explicit opportunity/threat policy.

`navigation.py:NavigationController.decide` currently chooses exploration goals,
routes, mining and collection as well as issuing controls. Calling that unchanged
policy after a Jev request would not demonstrate that Jev selected the behavior.
The new controller must expose actual selector choices and record which selected
goal produced each motor commitment. It must not fall back to the deterministic
task policy after a bad model choice.

Contract:

- Candidate generation consumes only the filtered policy observation and memory
  of previously observed cells. Current facts and remembered facts retain ticks.
  Unknown space is never treated as support or air.
- Exclude course/seed/specification metadata, evaluator target coordinates,
  authoritative success labels, hidden drops and the privileged course route.
- Offer bounded local waypoints and bounded look/mining/stop behaviors. A waypoint
  route uses known traversable cells, at most four graph edges and a small local
  envelope; freeze the exact envelope and menu before paid screening. Never offer
  a full-map goto-log or complete-course action.
- Publish candidate construction, coordinate conventions, ordering and IDs.
  Generate alternatives from observed geometry, not a preselected expert answer.
  The model sees the offered descriptions; the expected label is separate.
- Use one shared motor for Jev and a separately named deterministic selector.
  The motor translates a chosen local goal into controls; the selector chooses
  the goal. Record both identities and their source hashes.
- Keep the initial one-second provider deadline, at most one outstanding request,
  maximum two requests per second, and bounded commitments no longer than forty
  ticks. Meaningful world changes invalidate a commitment early.
- Fresh motor commands retain the qualified three-tick apply offset, five-tick
  duration and immediate geometry checks. Revalidate a returned choice against
  current observations; do not apply stale geometry from the request snapshot.
- Stop releases controls. A model-selected idle or unhelpful valid choice remains
  a model outcome; the deterministic task policy must not rescue it. Invalid or
  late responses release controls and retain their failure evidence.
- Keep the existing native physics, causal break/drop/pickup scorer, course time
  limits and reset/body qualification. Do not inherit model qualification from
  the deterministic motor's 80/80 result.

Finish this step by freezing the exact menu, observation schema, source hashes
and request/commitment budgets alongside executable candidate construction.

## 2. Implement and verify hybrid arbitration and shared motor integration

Planned new modules: `navigation_choices.py` for the bounded menu and permitted
summary; `model_navigation.py` for asynchronous selection and commitment handling;
`model_navigation_runner.py` for separately identified unscored experiments.
Preserve the existing qualified navigation controller/runner and historical proof.

Optional C1 tandem controller support is now implemented with independent
asynchronous model lanes, versioned Choice-planner context, stale-result rejection,
separate role budgets/accounting and recorded degraded/failure policies. The
original single-model path remains available. Full structured planning, opportunity
suspension/resumption and the wider navigation integration remain pending.

Add a separate hybrid plan/commitment contract and arbiter. Implement cancellation,
plan-revision checks, threat/opportunity priority, bounded diversion budgets,
cooldowns and revalidation before resuming the suspended subgoal. Start with fake
providers for timing/interleaving tests, then separate planner/Jev adapters. A
scripted strategic objective is a named test condition, not a larger-LLM result.
Immediate local safety cannot wait for either provider. Combat remains gated by
its pending body/sensor/action qualification.

Reuse the existing observed-map/path/rotation/fresh-motion functions where their
contracts apply. Test the new integration for hidden-state exclusion, candidate
identity, expired choices, changed geometry, one-block ascent/descent, stable
commitments, release/cancellation, provider deadlines, cost caps and recording
failures. Run applicable regressions and code review before native trials.
The new deterministic selector must use the same new motor/action profile as Jev.

## 3. Screen navigation and tactical interruptions on recorded observations

Freeze forty to sixty development states from retained C2–C5 policy recordings,
including exploration, facing errors, walls, rises/descents, ledges, mining and
collection. Keep layouts and adjacent frames identified; do not count neighboring
frames as independent layouts. Review valid action sets from observed geometry;
Jev is not the ground truth. Record limitations where a snapshot lacks history.

Run original/reversed/seeded candidate orders with labels excluded from requests,
the frozen one-second deadline and bounded calls. Preserve every outcome and
charged request. Stop on readiness/deadline failures; do not silently retry or
relax the profile. Only a viable configuration advances to native smoke trials.
The earlier synthetic screen remains separate evidence.

Include bounded pickup-diversion decisions, return-to-subgoal decisions and stale
plan/decision interleavings. Threat arbitration can initially use controller-only
injected events with that limitation recorded; it does not qualify live fighting.

## 4. Run bounded native hybrid development smoke trials

Start with five C2 development variants, then C3/C4/C5 if the selected
configuration remains viable. Compare the new deterministic selector and Jev
under the same body, observations, menu, timing and motor. Keep historical V8
whole-system results separately identified rather than treating them as an
identical-motor component comparison.

Verify that an opportunistic pickup suspends and then resumes a strategic goal,
and that slow planner responses cannot overwrite a newer plan or urgent tactical
context. Record scripted-planner and actual larger-LLM-planner trials separately.

Keep the game ticking during inference, enforce per-batch provider/request/cost
budgets, qualify readiness before arena work, and admit one arena command at a
time. Restart before exceeding retained body-identity limits. Preserve failures,
source hashes, every policy tick, causal chains and cleanup back to frozen C1.

## 5. Freeze and audit the paired development comparison

For a viable frozen configuration, run twenty paired development layouts per
C2–C5 course, retaining every attempted outcome and isolated timing conditions.
Use at least 18/20 per course as the declared development navigation target;
never pool smoke attempts or earlier configurations into that denominator.

Report task success, completion ticks, falls/stuck behavior, illegal/rejected
actions, decision switching, observation-to-action latency, provider failures and
reported/settled cost separately. Audit raw recordings, policy coverage, causal
drop UUIDs, source/configuration hashes and SQLite outcomes before gate changes.
Also report interruption/resumption success, planner versus tactical call counts,
stale-plan/decision discards and qualified threat-response timing. Compare the
same planner with/without Jev and the same shared motor where making component
claims; keep whole-system and controlled-component tables distinct.
Freeze the result before considering fresh holdouts. Learned-policy data/training,
construction, camera experiments and the Baritone client baseline remain later
work; they are not established by a successful Jev selector experiment.
