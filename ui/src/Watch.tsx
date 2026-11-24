// Where the bot is: which server to join, its address, and for an arena the
// command that stands you in the spectator box.
import { Badge, Button } from "@homenet/ui";
import { CopyButton } from "./fields";
import { type Where, watchFor } from "./forms";

type Rec = Record<string, unknown>;
const NAMES = { lab: "Lab server", play: "Play server" } as const;
export type ServerName = keyof typeof NAMES;

/** Both servers: running or not, the address to join, and a Start button. */
export function ServerChips({
  states,
  where,
  start,
}: {
  states: Record<ServerName, string>;
  where: Where;
  start: (which: ServerName) => void;
}) {
  return (
    <div className="servers">
      {(Object.keys(NAMES) as ServerName[]).map((which) => {
        const state = states[which];
        const address = where.servers?.[which]?.address;
        const lan = where.servers?.[which]?.lan_address;
        return (
          <div className="server" key={which} title={where.servers?.[which]?.label}>
            <Badge tone={state === "running" ? "working" : "quiet"}>
              {NAMES[which]}: {state}
            </Badge>
            {address && (
              <span className="address" title="Join from this PC">
                <code>{address}</code>
                <CopyButton text={address} />
              </span>
            )}
            {lan && (
              <span className="address" title="Join from another PC on your network">
                LAN <code>{lan}</code>
                <CopyButton text={lan} />
              </span>
            )}
            {state !== "running" && state !== "starting" && (
              <Button variant="secondary" onClick={() => start(which)}>
                Start
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** How to watch the job that is running now. */
export function WatchCard({ job, where }: { job: Rec | null; where: Where }) {
  if (!job)
    return (
      <p className="watch-idle">
        Lab missions run on the <b>lab server</b> (a flat test world,{" "}
        <code>{where.servers?.lab?.address ?? "…"}</code>). Arenas and combat run
        on the <b>play server</b> (<code>{where.servers?.play?.address ?? "…"}</code>
        ). Joining the wrong one shows an empty world.
      </p>
    );
  const w = watchFor(job, where);
  return (
    <ol className="watch-steps">
      <li>
        In Minecraft: Multiplayer → Direct Connection →{" "}
        {w.address ? (
          <>
            <code>{w.address}</code> <CopyButton text={w.address} />
          </>
        ) : (
          "(address unknown)"
        )}{" "}
        ({w.server === "play" ? "the play server" : "the lab server, a flat test world"}).
        {w.lanAddress && (
          <>
            {" "}
            From another PC on your network: <code>{w.lanAddress}</code>{" "}
            <CopyButton text={w.lanAddress} />
          </>
        )}
      </li>
      {w.humans.length > 0 && (
        <li>
          The match waits for {w.humans.join(", ")}: once in the game, type{" "}
          <code>/mcb ready</code> <CopyButton text="/mcb ready" /> (or just{" "}
          <code>ready</code> in chat). It starts when everyone named is ready.
        </li>
      )}
      {w.arena && w.teleport && (
        <li>
          Stand in the {w.arena.replaceAll("_", " ")} spectator box: type{" "}
          <code>{w.teleport}</code> <CopyButton text={w.teleport} />
        </li>
      )}
      {w.arena && w.built === false && (
        <li className="warn">
          The {w.arena.replaceAll("_", " ")} arena is not built on the play server,
          so this run will fail. Run “Build an arena” first.
        </li>
      )}
      {!w.arena && (
        <li>
          Press Tab to see the bot's name, then type <code>/tp @s &lt;name&gt;</code>{" "}
          to go to it.
        </li>
      )}
    </ol>
  );
}
