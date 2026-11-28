# Arena lobby: game menu, signs and floating board

The arena server's lobby (`designs.lobby`) is used by kids aged 12 to 16 on **stock Java Edition 26.3
clients**. Nothing may need a client mod and nobody should have to type a command. This doc is the
contract between the Python lobby (which owns every rule and every word on screen) and the mod
(which only draws what it is sent and passes clicks back).

## What players see

- **A game menu.** It uses the vanilla server-sent dialog screens (`net.minecraft.server.dialog`).
  Players open it in three ways:
  - right-click the **Game Menu compass**, which the mod keeps in hotbar slot 9 while they are in the lobby;
  - press **G** (Quick Actions), or click **Arena Menu** in the Esc screen
    (a static dialog in `#minecraft:quick_actions` and `#minecraft:pause_screen_additions`);
  - type `/mcb menu`.
- **Physical stations** for the two things kids do most:
  - four **team booths** on the west wall (sign and stone button in front of team-coloured concrete);
  - one **READY** booth on the east wall;
  - a **help sign** on the south wall.

  The mod rewrites the sign text live from what Python sends.
- **A floating board**: a vanilla text display above the spawn showing the map, mode, rules, who
  the game is waiting for, and the countdown.
- **The sidebar**: the existing `arena_board`, now in plain words with no revision numbers.

Every host-only control lives only in the host's menu. A non-host never sees one.

## Station layout

Coordinates are local to the lobby arena's minimum corner. The lobby is 25 × 12 × 25, the floor is at
y = 1, and players stand at y = 2. Both `designs.LOBBY_STATIONS` and `ArenaLobbyStations.STATIONS`
list these, and a Python test keeps them equal.

| id | sign (wall sign) | button | backing (in the wall) | facing | click payload |
|---|---|---|---|---|---|
| `join_red` | (2, 3, 7) | (2, 2, 7) | `red_concrete` x=1, y 2..4, z 6..8 | east | `{"action":"join","team":"red"}` |
| `join_blue` | (2, 3, 11) | (2, 2, 11) | `blue_concrete` x=1, y 2..4, z 10..12 | east | `{"action":"join","team":"blue"}` |
| `join_green` | (2, 3, 15) | (2, 2, 15) | `green_concrete` x=1, y 2..4, z 14..16 | east | `{"action":"join","team":"green"}` |
| `join_yellow` | (2, 3, 19) | (2, 2, 19) | `yellow_concrete` x=1, y 2..4, z 18..20 | east | `{"action":"join","team":"yellow"}` |
| `ready` | (22, 3, 12) | (22, 2, 12) | `lime_concrete` x=23, y 2..4, z 11..13 | west | `{"action":"ready_toggle"}` |
| `help` | (12, 3, 22) | none | none | north | opens the menu (handled by the mod, no Python) |

- Signs are `minecraft:dark_oak_wall_sign[facing=…]`. The mod writes their text with glowing text and waxes them, so the vanilla edit screen never opens.
- Buttons are `minecraft:stone_button[face=wall,facing=…]`.
- Right-clicking the sign or its button dispatches the payload (plus `"station": id`) through `McbCommand.dispatch(player, "lobby", …)`.
- A button click returns `PASS`, so the button visibly presses. A sign click returns `SUCCESS`.
- The floating text display sits at local (12.5, 5.5, 12.5), billboard `center`.

The design carries `LOBBY_VERSION`. If the built lobby's recorded version differs (a sidecar
`arena-lobby-version.json` in the instance runtime dir), `world._lobby` removes the lobby and rebuilds it.

## Op `arena_lobby_view` (protocol 7)

Python sends this op whenever what players see changes. Every field except `name` is optional, and an
unknown field anywhere is refused (`bad_request`), like every other arena op.

```json
{
  "name": "lobby",
  "signs":   {"join_red": {"lines": [{"text": "RED TEAM", "color": "red", "bold": true}, …≤4]}, …},
  "display": [{"text": "CROSSROADS", "color": "gold", "bold": true}, …≤8],
  "menus":   {"Ann": {"home": "home", "screens": {"home": {…}, …}}, "Bob": null},
  "show":    {"Ann": {"screen": "home", "notice": {"text": "You joined Red", "color": "green"}}},
  "close":   ["Ann", "Bob"],
  "announce": [{"players": ["Ann"], "title": "Settings changed", "subtitle": "Map is now Vault: ready up again"}]
}
```

- `name` must be a registered arena (the lobby).
- `signs`: station id → `{lines}` with at most 4 lines. Each line is `{text ≤ 24, color?, bold?}`, and colour is one of the 16 chat colours.
  Python keeps lines to about 15 characters, which is what fits on a sign.
- `display`: at most 8 lines, each `{text ≤ 64, color?, bold?}`. An empty list removes the text display.
- `menus`: player name → a menu, or `null` to drop it. A menu replaces the player's cached menu
  whole. The mod keeps menus by lower-cased name and drops one when the player disconnects.
- `show`: player name → `{screen, notice?}`. The mod opens that screen of the player's cached menu now. The
  `menus` in the same request are applied first. `notice` (`{text ≤ 200, color?}`) appears as the
  first body line of that screen for this one showing only.
- `close`: those players' dialogs are cleared (`ClientboundClearDialogPacket`).
- `announce`: each entry sends `title` (≤ 40) and `subtitle` (≤ 80) as a screen title to `players`.
- A player who is not online is skipped silently, never refused.
- The answer is `{"name", "menus": n, "shown": n, "signs": n}`.

### Menu

```json
{"home": "home", "screens": {"<id>": <screen>, …}}
```

- Screen ids match `[a-z0-9_]{1,32}`.
- A menu has at most 48 screens, and `home` must be one of them.

### Screen

```json
{
  "title": "MCB Arena",
  "body": [{"text": "Map: Crossroads · Team Battle", "color": "white", "bold": false}],
  "inputs": [<input>, …],
  "buttons": [<button>, …],
  "columns": 2,
  "exit": <button>
}
```

| field | limits | default | renders as |
|---|---|---|---|
| `title` | ≤ 64 | | dialog title |
| `body` | ≤ 12 lines, text ≤ 200 | `[]` | one `plain_message` per line |
| `inputs` | ≤ 8 | `[]` | dialog inputs |
| `buttons` | ≤ 24 | `[]` | `multi_action` buttons |
| `columns` | 1..3 | 2 | |
| `exit` | | "Close" button | bottom (exit) button; Esc does the same |

### Input

Every input has `key` (`[a-z_]{1,32}`, unique in the screen), `kind` and `label` (≤ 64).

| `kind` | further fields | dialog input |
|---|---|---|
| `dropdown` | `options`: 1..32 of `{id ≤ 48, label ≤ 64}`; `initial`: an option id | `single_option` |
| `toggle` | `initial`: bool | `boolean` |
| `text` | `initial`: str ≤ 32; `max_length`: 1..32 | `text` |
| `slider` | `min`, `max`, `step`, `initial`: ints (min < max, step ≥ 1) | `number_range` |

### Button

A button has `label` (≤ 40), `tooltip` (≤ 200, optional), `color` (optional chat colour, default white)
and `width` (40..300, optional). It also has **exactly one** of:

| field | what the mod does |
|---|---|
| `go: "<screen id>"` | shows that screen from the cache, with no Python round trip. The id must exist in the menu. |
| `action: {…}` | sends a `minecraft:custom` click. The mod dispatches `payload + {"menu_screen": <current screen id>}` to Python as a `lobby` player command. `after_action` is `wait_for_response`. |
| `submit: {…}` | like `action`, but with the screen's inputs merged in under their keys (dynamic custom click). Dropdown → its option id (string); toggle → bool; text → string; slider → int. |
| `close: true` | closes the dialog. |

- Payload values are flat. Keys match `[a-z_]{1,32}`, and values are strings (≤ 64), ints or bools.
- The click packet carries only a token (`<screen id>#<button index>`, plus the input values for
  `submit`). The mod looks the payload up in its cache, so the client never sends a payload of its own.
- If Python has not answered with a `show` for that player within 60 ticks, the mod shows the screen
  again with the notice "The game didn't answer. Try again." in red.

### As built (where the mod departs from the above)

- **No `wait_for_response`.** Every dialog's after-action is `none`. A 26.3 server cannot clear the
  client's "waiting for response" screen. Every button (`go` and `close` included) sends a click, and
  while a dispatched action waits for its answer, a second action click is ignored.
- **The click token is under `@t`**, not `t`, so an input keyed `t` can never overwrite it. There are also
  internal tokens `<screen>#x` (exit button), `!close` and `!open`.
- **A screen with no buttons is drawn as a notice dialog**, because a multi-action dialog needs at least one button.
- **`show` for a player with no cached menu is skipped.** Menus are cached only for online real players.
- **An `announce` needs a title or a subtitle**; either one alone is enough.
- **The lobby plan may use wall signs and buttons.** `plan.py` allows `*_wall_sign` and `*_button` only
  in the plan named `lobby`, and treats them as passable.

### Opening the menu

- The compass, G, the Esc button, `/mcb menu` and the help sign all show the cached `home` screen.
- If the player has no cached menu yet, the mod shows a notice ("Loading the game menu…") and
  dispatches `{"action": "menu"}`. Python answers with that player's menu and `show: home`.

## Python side

- `mclab.arena_lobby_menu`, a pure module:
  - `menu_for(snapshot, player, *, kits)` builds a player's menu;
  - `sign_lines(snapshot)` and `display_lines(snapshot)` build the shared text.

  Every label is plain English. There are no revision numbers and no ids.
- `LobbyPublisher`:
  - diffs what it last sent;
  - sends one `arena_lobby_view` per change;
  - on `tick()`, republishes when the lobby revision, phase, countdown second or online players change
    (dashboard edits arrive through the shared lobby file);
  - opens `recap` for the lobby's humans when a match ends and closes everyone's menu when a match starts;
  - announces "Settings changed: ready up again" to players whose ready status was cleared.
- Actions from the menu carry `menu_screen`. After every lobby action, the ingress answers with
  `show: {actor: {screen, notice}}`:
  - the screen is `then` if the payload has one, else `menu_screen`;
  - the notice is the result message in green, or the error in red.
- `ArenaLobby.apply_settings` (action `settings`) applies every host setting from the settings
  form in **one** revision, so ready players are reset once, not once per field.

## Menu screens (what Python builds)

| id | who | contents |
|---|---|---|
| `home` | everyone | status lines (map and mode, rules, your team and whether you're ready, who the game is waiting for or the countdown); buttons that depend on phase and seat: Join a Team, I'm Ready / Not Ready, Switch Team, Pick My Kit, Watch, Leave Game, Play Again, See Results, Game Info. The host also gets Game Settings, Choose Map, Bots, Allied Mobs and Give Host To…, plus Reopen Lobby when the lobby is frozen. |
| `teams` | everyone | one button per team on the map: "Red Team (2/4)", with the members as the tooltip. A full team is grey with a "This team is full" tooltip. Your own team says so. |
| `kits` | everyone | in Choose Your Own mode, one button per kit in the host's pool plus Random, with its items as the tooltip. In any other kit mode, an explanation of how kits are chosen. |
| `info` | everyone | every setting, read-only |
| `recap` | everyone | last match result; Play Again, Switch Team, Watch |
| `settings` | host | form: mode (only modes the map supports), lives, kit mode, kit for everyone, re-roll on respawn, mob waves, uneven teams → Save |
| `maps` | host | one button per map with teams × players and modes as the tooltip |
| `bots`, `bot_add`, `bot_<n>` | host | the bot list; add a bot (name and team); rename, move or remove one bot |
| `mobs` | host | allied mob groups with Remove; add a group (team, mob, count) |
| `host` | host | Give host to … (each other human) |
