// Visual slot-based Arena Kit Builder backed by /api/kits*.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card } from "@homenet/ui";
import { api } from "./api";
import {
  type CatalogItem,
  DEFAULT_CATALOG,
  type KitCatalog,
  type KitItemSpec,
  type KitRecord,
  type KitSlots,
  applicableEnchantments,
  canAddEnchantment,
  canDeleteKit,
  canEditKit,
  consumableCounts,
  emptySlots,
  estimateKitPower,
  formatEnchantment,
  itemsToSlots,
  powerTierTone,
  romanNumeral,
  setItemEnchantment,
  slotsToItems,
  validateDraftKit,
} from "./kits";

type KitsListResponse = KitRecord[] | { kits: KitRecord[]; catalog?: KitCatalog };

export type KitBuilderProps = {
  selectedKitId?: string;
  onSelectKit?: (kit: KitRecord) => void;
  onChange?: (kits: KitRecord[]) => void;
};

type EnchantPickerProps = {
  item: KitItemSpec;
  catalog: KitCatalog;
  disabled: boolean;
  onChange: (next: KitItemSpec) => void;
};

function EnchantPicker({ item, catalog, disabled, onChange }: EnchantPickerProps) {
  const options = useMemo(
    () => applicableEnchantments(item.item, catalog),
    [item.item, catalog],
  );
  const current = item.enchantments || {};
  const [selectedEnch, setSelectedEnch] = useState("");
  const [level, setLevel] = useState(1);

  if (options.length === 0) return null;

  const activeMeta = options.find((e) => e.id === selectedEnch);
  const addCheck = activeMeta
    ? canAddEnchantment(item, activeMeta.id, level, catalog)
    : { ok: false, reason: "Select an enchantment" };

  return (
    <div style={{ marginTop: 8, display: "grid", gap: 6 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
        {Object.entries(current).map(([enchId, lvl]) => (
          <span
            key={enchId}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 8px",
              borderRadius: 6,
              border: "1px solid var(--lab-border)",
              background: "#1b2630",
              fontSize: 12,
            }}
          >
            {formatEnchantment(enchId, lvl, catalog)}
            {!disabled && (
              <button
                type="button"
                className="link"
                aria-label={`Remove ${formatEnchantment(enchId, lvl, catalog)}`}
                onClick={() => onChange(setItemEnchantment(item, enchId, null, catalog))}
              >
                ✕
              </button>
            )}
          </span>
        ))}
      </div>
      {!disabled && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
          <select
            aria-label={`Add enchantment to ${item.item}`}
            value={selectedEnch}
            onChange={(e) => {
              const nextId = e.target.value;
              setSelectedEnch(nextId);
              setLevel(1);
            }}
            style={{ minWidth: 160 }}
          >
            <option value="">+ Enchantment…</option>
            {options.map((ench) => {
              const check = canAddEnchantment(item, ench.id, 1, catalog);
              return (
                <option key={ench.id} value={ench.id} disabled={!check.ok}>
                  {ench.label} (max {romanNumeral(ench.max_level)})
                  {!check.ok && check.reason ? ` — ${check.reason}` : ""}
                </option>
              );
            })}
          </select>
          {activeMeta && (
            <>
              <select
                aria-label={`Enchantment level for ${activeMeta.label}`}
                value={level}
                onChange={(e) => setLevel(Number(e.target.value))}
              >
                {Array.from({ length: activeMeta.max_level }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={n}>
                    {romanNumeral(n)}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                variant="secondary"
                disabled={!addCheck.ok}
                onClick={() => {
                  onChange(setItemEnchantment(item, activeMeta.id, level, catalog));
                  setSelectedEnch("");
                  setLevel(1);
                }}
              >
                Add
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

type SingleSlotEditorProps = {
  label: string;
  value: KitItemSpec | null;
  choices: CatalogItem[];
  catalog: KitCatalog;
  disabled: boolean;
  onChange: (next: KitItemSpec | null) => void;
};

function SingleSlotEditor({
  label,
  value,
  choices,
  catalog,
  disabled,
  onChange,
}: SingleSlotEditorProps) {
  return (
    <div
      className="field"
      style={{
        padding: 12,
        border: "1px solid var(--lab-border)",
        borderRadius: 8,
        background: "#111820",
      }}
    >
      <label>{label}</label>
      <select
        aria-label={label}
        disabled={disabled}
        value={value?.item ?? ""}
        onChange={(e) => {
          const itemId = e.target.value;
          if (!itemId) onChange(null);
          else onChange({ item: itemId, count: 1 });
        }}
      >
        <option value="">None</option>
        {choices.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
      </select>
      {value && (
        <EnchantPicker
          item={value}
          catalog={catalog}
          disabled={disabled}
          onChange={(next) => onChange(next)}
        />
      )}
    </div>
  );
}

export function KitBuilder({ selectedKitId, onSelectKit, onChange }: KitBuilderProps) {
  const [kits, setKits] = useState<KitRecord[]>([]);
  const [catalog, setCatalog] = useState<KitCatalog>(DEFAULT_CATALOG);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // Active editor state: editingId === "__new__" for unsaved custom kit
  const [editingId, setEditingId] = useState<string>("");
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftSlots, setDraftSlots] = useState<KitSlots>(emptySlots());

  const loadDraftFromKit = useCallback(
    (kit: KitRecord, cat: KitCatalog) => {
      setEditingId(kit.id);
      setDraftName(kit.name);
      setDraftDescription(kit.description || "");
      setDraftSlots(itemsToSlots(kit.items, cat));
      onSelectKit?.(kit);
    },
    [onSelectKit],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await api<KitsListResponse>("/api/kits");
      const list = Array.isArray(response) ? response : response.kits || [];
      let nextCatalog = !Array.isArray(response) && response.catalog ? response.catalog : catalog;
      if (Array.isArray(response) || !response.catalog) {
        try {
          nextCatalog = await api<KitCatalog>("/api/kits/catalog");
        } catch {
          // Fallback to DEFAULT_CATALOG if /api/kits/catalog is not separately mounted.
        }
      }
      setCatalog(nextCatalog);
      setKits(list);
      onChange?.(list);
      setEditingId((currentId) => {
        if (currentId === "__new__") return currentId;
        const wanted =
          list.find((k) => k.id === currentId) ||
          (selectedKitId ? list.find((k) => k.id === selectedKitId) : undefined) ||
          list[0];
        if (wanted) {
          setDraftName(wanted.name);
          setDraftDescription(wanted.description || "");
          setDraftSlots(itemsToSlots(wanted.items, nextCatalog));
          return wanted.id;
        }
        return "";
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [catalog, onChange, selectedKitId]);

  useEffect(() => {
    void refresh();
  }, []);

  const selectedKit = useMemo(
    () => kits.find((k) => k.id === editingId) || null,
    [kits, editingId],
  );
  const isNew = editingId === "__new__";
  const isReadOnly = !isNew && selectedKit !== null && !canEditKit(selectedKit);

  const draftItems = useMemo(() => slotsToItems(draftSlots), [draftSlots]);
  const counts = useMemo(() => consumableCounts(draftItems, catalog), [draftItems, catalog]);
  const livePower = useMemo(() => estimateKitPower(draftItems, catalog), [draftItems, catalog]);
  const validationErrors = useMemo(
    () =>
      isReadOnly
        ? []
        : validateDraftKit(
            {
              name: draftName,
              description: draftDescription,
              items: draftItems,
              builtin: false,
            },
            catalog,
          ),
    [catalog, draftDescription, draftItems, draftName, isReadOnly],
  );

  const weaponChoices = useMemo(
    () => catalog.items.filter((i) => i.group === "melee" || i.group === "ranged"),
    [catalog],
  );
  const meleeChoices = useMemo(
    () => catalog.items.filter((i) => i.group === "melee"),
    [catalog],
  );
  const rangedChoices = useMemo(
    () => catalog.items.filter((i) => i.group === "ranged" || i.group === "melee"),
    [catalog],
  );
  const armorBySlot = useMemo(
    () => ({
      helmet: catalog.items.filter((i) => i.category === "helmet"),
      chestplate: catalog.items.filter((i) => i.category === "chestplate"),
      leggings: catalog.items.filter((i) => i.category === "leggings"),
      boots: catalog.items.filter((i) => i.category === "boots"),
    }),
    [catalog],
  );
  const ammoChoices = useMemo(() => catalog.items.filter((i) => i.group === "ammo"), [catalog]);
  const foodChoices = useMemo(() => catalog.items.filter((i) => i.group === "food"), [catalog]);
  const potionChoices = useMemo(
    () => catalog.items.filter((i) => i.group === "potion"),
    [catalog],
  );

  const builtins = useMemo(() => kits.filter((k) => k.builtin), [kits]);
  const customs = useMemo(() => kits.filter((k) => !k.builtin), [kits]);

  const startNewKit = () => {
    setError("");
    setNotice("");
    setEditingId("__new__");
    setDraftName("Custom Kit");
    setDraftDescription("");
    setDraftSlots(
      itemsToSlots(
        [
          { item: "minecraft:iron_sword", count: 1 },
          { item: "minecraft:cooked_beef", count: 16 },
        ],
        catalog,
      ),
    );
  };

  const saveDraft = async () => {
    if (isReadOnly || validationErrors.length > 0) return;
    setSaving(true);
    setError("");
    setNotice("");
    const body = {
      name: draftName.trim(),
      description: draftDescription.trim(),
      items: draftItems,
    };
    try {
      const saved = isNew
        ? await api<KitRecord>("/api/kits", "POST", body)
        : await api<KitRecord>(`/api/kits/${encodeURIComponent(editingId)}`, "PUT", body);
      setNotice(isNew ? `Created kit "${saved.name}".` : `Saved kit "${saved.name}".`);
      setEditingId(saved.id);
      await refresh();
      loadDraftFromKit(saved, catalog);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const duplicateKit = async (kit: KitRecord) => {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const copy = await api<KitRecord>(
        `/api/kits/${encodeURIComponent(kit.id)}/duplicate`,
        "POST",
        { name: `${kit.name} Copy`.slice(0, catalog.caps.max_name_len) },
      );
      setNotice(`Duplicated "${kit.name}" as "${copy.name}".`);
      setEditingId(copy.id);
      await refresh();
      loadDraftFromKit(copy, catalog);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const deleteKit = async (kit: KitRecord) => {
    if (!canDeleteKit(kit)) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/kits/${encodeURIComponent(kit.id)}`, "DELETE");
      setNotice(`Deleted kit "${kit.name}".`);
      if (editingId === kit.id) setEditingId("");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const toggleOffhand = (itemId: string, checked: boolean) => {
    setDraftSlots((prev) => {
      const filtered = prev.offhand.filter((o) => o.item !== itemId);
      if (checked) filtered.push({ item: itemId, count: 1 });
      return { ...prev, offhand: filtered };
    });
  };

  const shieldItem = draftSlots.offhand.find((o) => o.item === "minecraft:shield") || null;
  const hasTotem = draftSlots.offhand.some((o) => o.item === "minecraft:totem_of_undying");

  return (
    <div className="workspace" style={{ alignItems: "start" }}>
      <div className="editor">
        {error && (
          <Alert title="Kit Builder error" tone="attention">
            {error}
          </Alert>
        )}
        {notice && (
          <Alert title="Kit Library" tone="ready">
            {notice}
          </Alert>
        )}

        <Card
          className="panel"
          title={
            isNew
              ? "New Custom Kit"
              : selectedKit
                ? `${selectedKit.name}${isReadOnly ? " (Built-in Starter Class)" : ""}`
                : "Arena Kit Builder"
          }
          description="Survival-legal gear loadouts with vanilla enchantment guardrails and consumable caps."
        >
          {isReadOnly && selectedKit && (
            <div style={{ marginBottom: 14, display: "flex", gap: 10, alignItems: "center" }}>
              <Badge tone="quiet">Built-in · Read-only</Badge>
              <Button
                type="button"
                variant="primary"
                disabled={saving}
                onClick={() => void duplicateKit(selectedKit)}
              >
                Duplicate to customize
              </Button>
            </div>
          )}

          {/* Power Tier + Live Cap Bar */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 10,
              alignItems: "center",
              padding: "10px 12px",
              borderRadius: 8,
              border: "1px solid var(--lab-border)",
              background: "#111820",
              marginBottom: 14,
            }}
          >
            <Badge tone={powerTierTone(livePower.tier)}>
              {livePower.tier} · {livePower.score} pts
            </Badge>
            <span style={{ fontSize: 12, color: "var(--lab-muted)" }}>
              Melee {livePower.breakdown.melee} · Ranged {livePower.breakdown.ranged} · Armor{" "}
              {livePower.breakdown.armor} · Ench {livePower.breakdown.enchantments} · Supplies{" "}
              {livePower.breakdown.consumables}
            </span>
          </div>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 12,
              fontSize: 12,
              marginBottom: 14,
            }}
          >
            <span className={counts.weapons > catalog.caps.max_weapons ? "warn" : ""}>
              Weapons: <strong>{counts.weapons}</strong>/{catalog.caps.max_weapons}
            </span>
            <span className={counts.arrows > catalog.caps.max_arrows ? "warn" : ""}>
              Arrows: <strong>{counts.arrows}</strong>/{catalog.caps.max_arrows}
            </span>
            <span className={counts.food > catalog.caps.max_food ? "warn" : ""}>
              Food: <strong>{counts.food}</strong>/{catalog.caps.max_food}
            </span>
            <span className={counts.goldenApples > catalog.caps.max_golden_apples ? "warn" : ""}>
              Golden Apples: <strong>{counts.goldenApples}</strong>/{catalog.caps.max_golden_apples}
            </span>
            <span className={counts.potions > catalog.caps.max_potions ? "warn" : ""}>
              Potions: <strong>{counts.potions}</strong>/{catalog.caps.max_potions}
            </span>
            <span className={counts.slotsUsed > catalog.caps.max_give ? "warn" : ""}>
              Slots: <strong>{counts.slotsUsed}</strong>/{catalog.caps.max_give}
            </span>
          </div>

          {livePower.bot_notes.length > 0 && (
            <div style={{ marginBottom: 14, fontSize: 12 }} className="warn">
              {livePower.bot_notes.map((note) => (
                <div key={note}>Bot note: {note}</div>
              ))}
            </div>
          )}

          {/* Metadata */}
          <div className="fields">
            <div className="field">
              <label htmlFor="kit-name">Kit name</label>
              <input
                id="kit-name"
                disabled={isReadOnly}
                maxLength={catalog.caps.max_name_len}
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="e.g. Heavy Vanguard"
              />
            </div>
            <div className="field">
              <label htmlFor="kit-desc">Description</label>
              <input
                id="kit-desc"
                disabled={isReadOnly}
                maxLength={catalog.caps.max_description_len}
                value={draftDescription}
                onChange={(e) => setDraftDescription(e.target.value)}
                placeholder="Short summary shown in lobby and recaps"
              />
            </div>
          </div>

          {/* Weapons */}
          <div className="field-group">
            <h4>Weapons</h4>
            <div className="fields">
              <SingleSlotEditor
                label="Primary Weapon"
                value={draftSlots.primary}
                choices={meleeChoices.concat(
                  catalog.items.filter((i) => i.group === "ranged"),
                )}
                catalog={catalog}
                disabled={isReadOnly}
                onChange={(primary) => setDraftSlots((s) => ({ ...s, primary }))}
              />
              <SingleSlotEditor
                label="Secondary / Ranged"
                value={draftSlots.secondary}
                choices={rangedChoices}
                catalog={catalog}
                disabled={isReadOnly}
                onChange={(secondary) => setDraftSlots((s) => ({ ...s, secondary }))}
              />
            </div>
            {draftSlots.extraWeapons.map((extra, idx) => (
              <div key={idx} style={{ marginBottom: 8 }}>
                <SingleSlotEditor
                  label={`Extra Weapon ${idx + 1}`}
                  value={extra}
                  choices={weaponChoices}
                  catalog={catalog}
                  disabled={isReadOnly}
                  onChange={(next) =>
                    setDraftSlots((s) => ({
                      ...s,
                      extraWeapons: next
                        ? s.extraWeapons.map((w, i) => (i === idx ? next : w))
                        : s.extraWeapons.filter((_, i) => i !== idx),
                    }))
                  }
                />
              </div>
            ))}
            {!isReadOnly && counts.weapons < catalog.caps.max_weapons && (
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  setDraftSlots((s) => ({
                    ...s,
                    extraWeapons: [...s.extraWeapons, { item: "minecraft:trident", count: 1 }],
                  }))
                }
              >
                + Add extra weapon
              </Button>
            )}
          </div>

          {/* Armor */}
          <div className="field-group">
            <h4>Armor</h4>
            <div className="fields">
              <SingleSlotEditor
                label="Helmet"
                value={draftSlots.helmet}
                choices={armorBySlot.helmet}
                catalog={catalog}
                disabled={isReadOnly}
                onChange={(helmet) => setDraftSlots((s) => ({ ...s, helmet }))}
              />
              <SingleSlotEditor
                label="Chestplate"
                value={draftSlots.chestplate}
                choices={armorBySlot.chestplate}
                catalog={catalog}
                disabled={isReadOnly}
                onChange={(chestplate) => setDraftSlots((s) => ({ ...s, chestplate }))}
              />
              <SingleSlotEditor
                label="Leggings"
                value={draftSlots.leggings}
                choices={armorBySlot.leggings}
                catalog={catalog}
                disabled={isReadOnly}
                onChange={(leggings) => setDraftSlots((s) => ({ ...s, leggings }))}
              />
              <SingleSlotEditor
                label="Boots"
                value={draftSlots.boots}
                choices={armorBySlot.boots}
                catalog={catalog}
                disabled={isReadOnly}
                onChange={(boots) => setDraftSlots((s) => ({ ...s, boots }))}
              />
            </div>
          </div>

          {/* Offhand */}
          <div className="field-group">
            <h4>Offhand</h4>
            <div className="fields">
              <div
                className="field"
                style={{
                  padding: 12,
                  border: "1px solid var(--lab-border)",
                  borderRadius: 8,
                  background: "#111820",
                }}
              >
                <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    disabled={isReadOnly}
                    checked={shieldItem !== null}
                    onChange={(e) => toggleOffhand("minecraft:shield", e.target.checked)}
                  />
                  Shield
                </label>
                {shieldItem && (
                  <EnchantPicker
                    item={shieldItem}
                    catalog={catalog}
                    disabled={isReadOnly}
                    onChange={(next) =>
                      setDraftSlots((s) => ({
                        ...s,
                        offhand: s.offhand.map((o) =>
                          o.item === "minecraft:shield" ? next : o,
                        ),
                      }))
                    }
                  />
                )}
              </div>
              <div
                className="field"
                style={{
                  padding: 12,
                  border: "1px solid var(--lab-border)",
                  borderRadius: 8,
                  background: "#111820",
                }}
              >
                <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    disabled={isReadOnly}
                    checked={hasTotem}
                    onChange={(e) =>
                      toggleOffhand("minecraft:totem_of_undying", e.target.checked)
                    }
                  />
                  Totem of Undying
                </label>
              </div>
            </div>
          </div>

          {/* Ammo */}
          <div className="field-group">
            <h4>Ammo</h4>
            <div style={{ display: "grid", gap: 8, margin: "12px 0" }}>
              {draftSlots.ammo.map((entry, idx) => (
                <div
                  key={idx}
                  style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}
                >
                  <select
                    disabled={isReadOnly}
                    value={entry.item}
                    onChange={(e) => {
                      const item = e.target.value;
                      setDraftSlots((s) => ({
                        ...s,
                        ammo: s.ammo.map((a, i) =>
                          i === idx
                            ? {
                                item,
                                count: a.count,
                                ...(item === "minecraft:tipped_arrow"
                                  ? { potion: a.potion || "minecraft:slowness" }
                                  : {}),
                              }
                            : a,
                        ),
                      }));
                    }}
                  >
                    {ammoChoices.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  {entry.item === "minecraft:tipped_arrow" && (
                    <select
                      disabled={isReadOnly}
                      value={entry.potion || "minecraft:slowness"}
                      onChange={(e) =>
                        setDraftSlots((s) => ({
                          ...s,
                          ammo: s.ammo.map((a, i) =>
                            i === idx ? { ...a, potion: e.target.value } : a,
                          ),
                        }))
                      }
                    >
                      {catalog.potions.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  )}
                  <input
                    type="number"
                    min={1}
                    max={catalog.caps.max_arrows}
                    disabled={isReadOnly}
                    value={entry.count}
                    style={{ width: 84 }}
                    onChange={(e) =>
                      setDraftSlots((s) => ({
                        ...s,
                        ammo: s.ammo.map((a, i) =>
                          i === idx ? { ...a, count: Math.max(1, Number(e.target.value) || 1) } : a,
                        ),
                      }))
                    }
                  />
                  {!isReadOnly && (
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        setDraftSlots((s) => ({
                          ...s,
                          ammo: s.ammo.filter((_, i) => i !== idx),
                        }))
                      }
                    >
                      Remove
                    </Button>
                  )}
                </div>
              ))}
              {!isReadOnly && (
                <div>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={counts.arrows >= catalog.caps.max_arrows}
                    onClick={() =>
                      setDraftSlots((s) => ({
                        ...s,
                        ammo: [...s.ammo, { item: "minecraft:arrow", count: 32 }],
                      }))
                    }
                  >
                    + Add arrows
                  </Button>
                </div>
              )}
            </div>
          </div>

          {/* Food */}
          <div className="field-group">
            <h4>Food & Golden Apples</h4>
            <div style={{ display: "grid", gap: 8, margin: "12px 0" }}>
              {draftSlots.food.map((entry, idx) => (
                <div
                  key={idx}
                  style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}
                >
                  <select
                    disabled={isReadOnly}
                    value={entry.item}
                    onChange={(e) =>
                      setDraftSlots((s) => ({
                        ...s,
                        food: s.food.map((f, i) =>
                          i === idx ? { item: e.target.value, count: f.count } : f,
                        ),
                      }))
                    }
                  >
                    {foodChoices.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  <input
                    type="number"
                    min={1}
                    max={
                      entry.item === "minecraft:golden_apple"
                        ? catalog.caps.max_golden_apples
                        : catalog.caps.max_food
                    }
                    disabled={isReadOnly}
                    value={entry.count}
                    style={{ width: 84 }}
                    onChange={(e) =>
                      setDraftSlots((s) => ({
                        ...s,
                        food: s.food.map((f, i) =>
                          i === idx ? { ...f, count: Math.max(1, Number(e.target.value) || 1) } : f,
                        ),
                      }))
                    }
                  />
                  {!isReadOnly && (
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        setDraftSlots((s) => ({
                          ...s,
                          food: s.food.filter((_, i) => i !== idx),
                        }))
                      }
                    >
                      Remove
                    </Button>
                  )}
                </div>
              ))}
              {!isReadOnly && (
                <div>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() =>
                      setDraftSlots((s) => ({
                        ...s,
                        food: [...s.food, { item: "minecraft:cooked_beef", count: 16 }],
                      }))
                    }
                  >
                    + Add food
                  </Button>
                </div>
              )}
            </div>
          </div>

          {/* Potions */}
          <div className="field-group">
            <h4>Potions</h4>
            <div style={{ display: "grid", gap: 8, margin: "12px 0" }}>
              {draftSlots.potions.map((entry, idx) => (
                <div
                  key={idx}
                  style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}
                >
                  <select
                    disabled={isReadOnly}
                    value={entry.item}
                    onChange={(e) =>
                      setDraftSlots((s) => ({
                        ...s,
                        potions: s.potions.map((p, i) =>
                          i === idx ? { ...p, item: e.target.value } : p,
                        ),
                      }))
                    }
                  >
                    {potionChoices.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  <select
                    disabled={isReadOnly}
                    value={entry.potion || "minecraft:strong_healing"}
                    onChange={(e) =>
                      setDraftSlots((s) => ({
                        ...s,
                        potions: s.potions.map((p, i) =>
                          i === idx ? { ...p, potion: e.target.value } : p,
                        ),
                      }))
                    }
                  >
                    {catalog.potions.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                  <input
                    type="number"
                    min={1}
                    max={catalog.caps.max_potions}
                    disabled={isReadOnly}
                    value={entry.count}
                    style={{ width: 72 }}
                    onChange={(e) =>
                      setDraftSlots((s) => ({
                        ...s,
                        potions: s.potions.map((p, i) =>
                          i === idx ? { ...p, count: Math.max(1, Number(e.target.value) || 1) } : p,
                        ),
                      }))
                    }
                  />
                  {!isReadOnly && (
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        setDraftSlots((s) => ({
                          ...s,
                          potions: s.potions.filter((_, i) => i !== idx),
                        }))
                      }
                    >
                      Remove
                    </Button>
                  )}
                </div>
              ))}
              {!isReadOnly && (
                <div>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={counts.potions >= catalog.caps.max_potions}
                    onClick={() =>
                      setDraftSlots((s) => ({
                        ...s,
                        potions: [
                          ...s.potions,
                          {
                            item: "minecraft:splash_potion",
                            count: 1,
                            potion: "minecraft:strong_healing",
                          },
                        ],
                      }))
                    }
                  >
                    + Add potion
                  </Button>
                </div>
              )}
            </div>
          </div>

          {/* Validation errors + Save actions */}
          {validationErrors.length > 0 && (
            <div style={{ marginTop: 12 }}>
              {validationErrors.map((err) => (
                <p key={err} className="field-error">
                  {err}
                </p>
              ))}
            </div>
          )}

          {!isReadOnly && (
            <div style={{ marginTop: 16, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Button
                type="button"
                variant="primary"
                loading={saving}
                disabled={validationErrors.length > 0 || saving}
                onClick={() => void saveDraft()}
              >
                {isNew ? "Create Kit" : "Save Changes"}
              </Button>
              {isNew && kits[0] && (
                <Button
                  type="button"
                  variant="secondary"
                  disabled={saving}
                  onClick={() => loadDraftFromKit(kits[0], catalog)}
                >
                  Cancel
                </Button>
              )}
            </div>
          )}
        </Card>
      </div>

      {/* Right sidebar: Starter Classes & Custom Kits */}
      <div className="activity">
        <Card
          className="panel"
          title="Starter Classes"
          description="Built-in survival-legal kits. Read-only — duplicate any class to customize."
        >
          {loading ? (
            <p>Loading kits…</p>
          ) : (
            <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
              {builtins.map((kit) => (
                <div
                  key={kit.id}
                  style={{
                    padding: 10,
                    borderRadius: 8,
                    border:
                      editingId === kit.id
                        ? "1px solid var(--lab-accent)"
                        : "1px solid var(--lab-border)",
                    background: "#111820",
                    display: "grid",
                    gap: 6,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <strong>{kit.name}</strong>
                    <Badge tone={powerTierTone(kit.power.tier)}>{kit.power.tier}</Badge>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => loadDraftFromKit(kit, catalog)}
                    >
                      Inspect
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={saving}
                      onClick={() => void duplicateKit(kit)}
                    >
                      Duplicate
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card
          className="panel"
          title="Custom Kits"
          description="Saved in runtime/arena-kits.json for lobbies and lab scenarios."
        >
          <div style={{ marginBottom: 10 }}>
            <Button type="button" variant="primary" disabled={saving} onClick={startNewKit}>
              + New Kit
            </Button>
          </div>
          {customs.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--lab-muted)" }}>
              No custom kits yet. Create one or duplicate a starter class.
            </p>
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {customs.map((kit) => (
                <div
                  key={kit.id}
                  style={{
                    padding: 10,
                    borderRadius: 8,
                    border:
                      editingId === kit.id
                        ? "1px solid var(--lab-accent)"
                        : "1px solid var(--lab-border)",
                    background: "#111820",
                    display: "grid",
                    gap: 6,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <strong>{kit.name}</strong>
                    <Badge tone={powerTierTone(kit.power.tier)}>{kit.power.tier}</Badge>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => loadDraftFromKit(kit, catalog)}
                    >
                      Edit
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={saving}
                      onClick={() => void duplicateKit(kit)}
                    >
                      Duplicate
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      disabled={saving}
                      onClick={() => void deleteKit(kit)}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
