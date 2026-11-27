// Pure TypeScript types, survival-legal validation/conflict helpers, slot
// conversion, and power-tier formatting for the Arena Kit Library UI.

export type PowerTier = "Starter" | "Balanced" | "Heavy" | "Champion";

export type KitMode =
  | "uniform"
  | "per_team"
  | "class_picker"
  | "everyone_random"
  | "random_shared";

export type KitItemSpec = {
  item: string;
  count: number;
  enchantments?: Record<string, number>;
  potion?: string;
};

export type PowerBreakdown = {
  melee: number;
  ranged: number;
  armor: number;
  offhand: number;
  enchantments: number;
  consumables: number;
};

export type KitPower = {
  score: number;
  tier: PowerTier;
  breakdown: PowerBreakdown;
  bot_notes: string[];
};

export type CompiledKit = {
  give: KitItemSpec[];
  clear_inventory: true;
  heal: true;
  wear: true;
};

export type KitRecord = {
  id: string;
  name: string;
  description: string;
  items: KitItemSpec[];
  builtin: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  power: KitPower;
  compiled?: CompiledKit;
};

export type CatalogItem = {
  id: string;
  label: string;
  category: string;
  group: "melee" | "ranged" | "armor" | "offhand" | "ammo" | "food" | "potion";
  slot: string;
  max_stack: number;
  damageable: boolean;
  requires_potion: boolean;
  power_points: number;
};

export type CatalogPotion = {
  id: string;
  label: string;
};

export type CatalogEnchantment = {
  id: string;
  label: string;
  max_level: number;
  categories: string[];
  conflicts: string[];
  power_per_level: number;
};

export type CatalogCaps = {
  max_give: number;
  max_enchantments_per_item: number;
  max_weapons: number;
  max_arrows: number;
  max_food: number;
  max_golden_apples: number;
  max_potions: number;
  max_name_len: number;
  max_description_len: number;
};

export type KitCatalog = {
  schema: number;
  kit_modes: KitMode[];
  random_sentinel: string;
  caps: CatalogCaps;
  items: CatalogItem[];
  potions: CatalogPotion[];
  enchantments: CatalogEnchantment[];
  tiers: { name: PowerTier; max_score: number | null }[];
};

export type KitSlots = {
  primary: KitItemSpec | null;
  secondary: KitItemSpec | null;
  extraWeapons: KitItemSpec[];
  helmet: KitItemSpec | null;
  chestplate: KitItemSpec | null;
  leggings: KitItemSpec | null;
  boots: KitItemSpec | null;
  offhand: KitItemSpec[];
  ammo: KitItemSpec[];
  food: KitItemSpec[];
  potions: KitItemSpec[];
};

export type ConsumableCounts = {
  weapons: number;
  arrows: number;
  food: number;
  goldenApples: number;
  potions: number;
  slotsUsed: number;
};

function titleId(fullId: string): string {
  const bare = fullId.includes(":") ? fullId.split(":", 2)[1] : fullId;
  return bare
    .split("_")
    .map((p) => (p ? p[0].toUpperCase() + p.slice(1) : p))
    .join(" ");
}

function buildDefaultItems(): CatalogItem[] {
  const items: CatalogItem[] = [];
  const meleeTiers: [string, number][] = [
    ["wooden", 6],
    ["stone", 9],
    ["copper", 10],
    ["golden", 8],
    ["iron", 12],
    ["diamond", 16],
    ["netherite", 18],
  ];
  for (const [tier, pts] of meleeTiers) {
    for (const weapon of ["sword", "axe", "spear"]) {
      const id = `minecraft:${tier}_${weapon}`;
      items.push({
        id,
        label: titleId(id),
        category: weapon,
        group: "melee",
        slot: "weapon",
        max_stack: 1,
        damageable: true,
        requires_potion: false,
        power_points: pts,
      });
    }
  }
  items.push(
    { id: "minecraft:mace", label: "Mace", category: "mace", group: "melee", slot: "weapon", max_stack: 1, damageable: true, requires_potion: false, power_points: 16 },
    { id: "minecraft:trident", label: "Trident", category: "trident", group: "melee", slot: "weapon", max_stack: 1, damageable: true, requires_potion: false, power_points: 14 },
    { id: "minecraft:bow", label: "Bow", category: "bow", group: "ranged", slot: "weapon", max_stack: 1, damageable: true, requires_potion: false, power_points: 11 },
    { id: "minecraft:crossbow", label: "Crossbow", category: "crossbow", group: "ranged", slot: "weapon", max_stack: 1, damageable: true, requires_potion: false, power_points: 11 },
  );

  const armorTiers: [string, Record<string, number>][] = [
    ["leather", { helmet: 2, chestplate: 4, leggings: 3, boots: 2 }],
    ["chainmail", { helmet: 3, chestplate: 6, leggings: 5, boots: 2 }],
    ["copper", { helmet: 3, chestplate: 7, leggings: 5, boots: 3 }],
    ["golden", { helmet: 3, chestplate: 6, leggings: 4, boots: 2 }],
    ["iron", { helmet: 4, chestplate: 8, leggings: 6, boots: 4 }],
    ["diamond", { helmet: 6, chestplate: 11, leggings: 9, boots: 6 }],
    ["netherite", { helmet: 7, chestplate: 13, leggings: 10, boots: 7 }],
  ];
  for (const [tier, pieces] of armorTiers) {
    for (const [piece, pts] of Object.entries(pieces)) {
      const id = `minecraft:${tier}_${piece}`;
      items.push({
        id,
        label: titleId(id),
        category: piece,
        group: "armor",
        slot: piece,
        max_stack: 1,
        damageable: true,
        requires_potion: false,
        power_points: pts,
      });
    }
  }
  items.push(
    { id: "minecraft:turtle_helmet", label: "Turtle Shell", category: "helmet", group: "armor", slot: "helmet", max_stack: 1, damageable: true, requires_potion: false, power_points: 5 },
    { id: "minecraft:shield", label: "Shield", category: "shield", group: "offhand", slot: "offhand", max_stack: 1, damageable: true, requires_potion: false, power_points: 6 },
    { id: "minecraft:totem_of_undying", label: "Totem of Undying", category: "totem", group: "offhand", slot: "offhand", max_stack: 1, damageable: false, requires_potion: false, power_points: 16 },
    { id: "minecraft:arrow", label: "Arrow", category: "ammo", group: "ammo", slot: "ammo", max_stack: 64, damageable: false, requires_potion: false, power_points: 0 },
    { id: "minecraft:spectral_arrow", label: "Spectral Arrow", category: "ammo", group: "ammo", slot: "ammo", max_stack: 64, damageable: false, requires_potion: false, power_points: 0 },
    { id: "minecraft:tipped_arrow", label: "Tipped Arrow", category: "ammo", group: "ammo", slot: "ammo", max_stack: 64, damageable: false, requires_potion: true, power_points: 0 },
  );

  for (const id of [
    "minecraft:cooked_beef",
    "minecraft:cooked_porkchop",
    "minecraft:cooked_chicken",
    "minecraft:cooked_mutton",
    "minecraft:bread",
    "minecraft:golden_carrot",
  ]) {
    items.push({
      id,
      label: titleId(id),
      category: "food",
      group: "food",
      slot: "food",
      max_stack: 64,
      damageable: false,
      requires_potion: false,
      power_points: 0,
    });
  }
  items.push(
    { id: "minecraft:golden_apple", label: "Golden Apple", category: "golden_apple", group: "food", slot: "food", max_stack: 64, damageable: false, requires_potion: false, power_points: 3 },
    { id: "minecraft:potion", label: "Potion", category: "potion", group: "potion", slot: "potion", max_stack: 1, damageable: false, requires_potion: true, power_points: 3 },
    { id: "minecraft:splash_potion", label: "Splash Potion", category: "potion", group: "potion", slot: "potion", max_stack: 1, damageable: false, requires_potion: true, power_points: 3 },
    { id: "minecraft:lingering_potion", label: "Lingering Potion", category: "potion", group: "potion", slot: "potion", max_stack: 1, damageable: false, requires_potion: true, power_points: 3 },
  );
  return items;
}

const DEFAULT_POTION_IDS = [
  "minecraft:healing",
  "minecraft:strong_healing",
  "minecraft:regeneration",
  "minecraft:long_regeneration",
  "minecraft:strong_regeneration",
  "minecraft:swiftness",
  "minecraft:long_swiftness",
  "minecraft:strong_swiftness",
  "minecraft:strength",
  "minecraft:long_strength",
  "minecraft:strong_strength",
  "minecraft:fire_resistance",
  "minecraft:long_fire_resistance",
  "minecraft:leaping",
  "minecraft:long_leaping",
  "minecraft:strong_leaping",
  "minecraft:harming",
  "minecraft:strong_harming",
  "minecraft:poison",
  "minecraft:long_poison",
  "minecraft:strong_poison",
  "minecraft:weakness",
  "minecraft:long_weakness",
  "minecraft:slowness",
  "minecraft:long_slowness",
  "minecraft:strong_slowness",
  "minecraft:turtle_master",
  "minecraft:long_turtle_master",
  "minecraft:strong_turtle_master",
  "minecraft:slow_falling",
  "minecraft:long_slow_falling",
];

const ARMOR_CATS = ["helmet", "chestplate", "leggings", "boots"];
const DAMAGEABLE_CATS = [
  "sword",
  "axe",
  "spear",
  "mace",
  "trident",
  "bow",
  "crossbow",
  ...ARMOR_CATS,
  "shield",
];

const CONFLICT_GROUPS: string[][] = [
  ["minecraft:sharpness", "minecraft:smite", "minecraft:bane_of_arthropods"],
  ["minecraft:density", "minecraft:breach", "minecraft:smite", "minecraft:bane_of_arthropods"],
  [
    "minecraft:protection",
    "minecraft:fire_protection",
    "minecraft:blast_protection",
    "minecraft:projectile_protection",
  ],
  ["minecraft:multishot", "minecraft:piercing"],
  ["minecraft:infinity", "minecraft:mending"],
  ["minecraft:loyalty", "minecraft:riptide"],
  ["minecraft:channeling", "minecraft:riptide"],
  ["minecraft:depth_strider", "minecraft:frost_walker"],
];

function buildDefaultEnchantments(): CatalogEnchantment[] {
  const raw: [string, number, string[], number][] = [
    ["minecraft:sharpness", 5, ["sword", "axe"], 3],
    ["minecraft:smite", 5, ["sword", "axe", "mace"], 2],
    ["minecraft:bane_of_arthropods", 5, ["sword", "axe", "mace"], 1],
    ["minecraft:knockback", 2, ["sword"], 2],
    ["minecraft:fire_aspect", 2, ["sword"], 2],
    ["minecraft:sweeping_edge", 3, ["sword"], 2],
    ["minecraft:power", 5, ["bow"], 3],
    ["minecraft:punch", 2, ["bow"], 2],
    ["minecraft:flame", 1, ["bow"], 3],
    ["minecraft:infinity", 1, ["bow"], 3],
    ["minecraft:quick_charge", 3, ["crossbow"], 2],
    ["minecraft:multishot", 1, ["crossbow"], 3],
    ["minecraft:piercing", 4, ["crossbow"], 2],
    ["minecraft:loyalty", 3, ["trident"], 2],
    ["minecraft:impaling", 5, ["trident"], 2],
    ["minecraft:riptide", 3, ["trident"], 2],
    ["minecraft:channeling", 1, ["trident"], 2],
    ["minecraft:density", 5, ["mace"], 3],
    ["minecraft:breach", 4, ["mace"], 3],
    ["minecraft:wind_burst", 3, ["mace"], 2],
    ["minecraft:protection", 4, ARMOR_CATS, 2],
    ["minecraft:fire_protection", 4, ARMOR_CATS, 2],
    ["minecraft:blast_protection", 4, ARMOR_CATS, 2],
    ["minecraft:projectile_protection", 4, ARMOR_CATS, 2],
    ["minecraft:thorns", 3, ARMOR_CATS, 2],
    ["minecraft:respiration", 3, ["helmet"], 1],
    ["minecraft:aqua_affinity", 1, ["helmet"], 1],
    ["minecraft:swift_sneak", 3, ["leggings"], 1],
    ["minecraft:feather_falling", 4, ["boots"], 2],
    ["minecraft:depth_strider", 3, ["boots"], 1],
    ["minecraft:soul_speed", 3, ["boots"], 1],
    ["minecraft:frost_walker", 2, ["boots"], 1],
    ["minecraft:unbreaking", 3, DAMAGEABLE_CATS, 1],
    ["minecraft:mending", 1, DAMAGEABLE_CATS, 1],
  ];
  return raw.map(([id, max_level, categories, power_per_level]) => {
    const conflicts = new Set<string>();
    for (const group of CONFLICT_GROUPS) {
      if (group.includes(id)) {
        for (const other of group) {
          if (other !== id) conflicts.add(other);
        }
      }
    }
    return {
      id,
      label: titleId(id),
      max_level,
      categories: [...categories],
      conflicts: [...conflicts].sort(),
      power_per_level,
    };
  });
}

export const DEFAULT_CATALOG: KitCatalog = {
  schema: 1,
  kit_modes: ["uniform", "per_team", "class_picker", "everyone_random", "random_shared"],
  random_sentinel: "random",
  caps: {
    max_give: 36,
    max_enchantments_per_item: 4,
    max_weapons: 4,
    max_arrows: 128,
    max_food: 64,
    max_golden_apples: 8,
    max_potions: 6,
    max_name_len: 40,
    max_description_len: 200,
  },
  items: buildDefaultItems(),
  potions: DEFAULT_POTION_IDS.map((id) => ({ id, label: titleId(id) })),
  enchantments: buildDefaultEnchantments(),
  tiers: [
    { name: "Starter", max_score: 44 },
    { name: "Balanced", max_score: 74 },
    { name: "Heavy", max_score: 124 },
    { name: "Champion", max_score: null },
  ],
};

function itemById(catalog: KitCatalog, id: string): CatalogItem | undefined {
  return catalog.items.find((i) => i.id === id);
}

function enchantById(catalog: KitCatalog, id: string): CatalogEnchantment | undefined {
  return catalog.enchantments.find((e) => e.id === id);
}

function cloneItem(entry: KitItemSpec): KitItemSpec {
  const out: KitItemSpec = { item: entry.item, count: entry.count };
  if (entry.enchantments && Object.keys(entry.enchantments).length > 0) {
    out.enchantments = { ...entry.enchantments };
  }
  if (entry.potion) out.potion = entry.potion;
  return out;
}

export function emptySlots(): KitSlots {
  return {
    primary: null,
    secondary: null,
    extraWeapons: [],
    helmet: null,
    chestplate: null,
    leggings: null,
    boots: null,
    offhand: [],
    ammo: [],
    food: [],
    potions: [],
  };
}

export function itemsToSlots(items: KitItemSpec[], catalog: KitCatalog): KitSlots {
  const slots = emptySlots();
  const melee: KitItemSpec[] = [];
  const ranged: KitItemSpec[] = [];

  for (const raw of items) {
    const entry = cloneItem(raw);
    const meta = itemById(catalog, entry.item);
    if (!meta) continue;
    if (meta.group === "melee") melee.push(entry);
    else if (meta.group === "ranged") ranged.push(entry);
    else if (meta.category === "helmet") slots.helmet = entry;
    else if (meta.category === "chestplate") slots.chestplate = entry;
    else if (meta.category === "leggings") slots.leggings = entry;
    else if (meta.category === "boots") slots.boots = entry;
    else if (meta.group === "offhand") slots.offhand.push(entry);
    else if (meta.group === "ammo") slots.ammo.push(entry);
    else if (meta.group === "food") slots.food.push(entry);
    else if (meta.group === "potion") slots.potions.push(entry);
  }

  if (melee.length > 0) {
    slots.primary = melee[0];
    if (ranged.length > 0) {
      slots.secondary = ranged[0];
      slots.extraWeapons = [...melee.slice(1), ...ranged.slice(1)];
    } else if (melee.length > 1) {
      slots.secondary = melee[1];
      slots.extraWeapons = melee.slice(2);
    }
  } else if (ranged.length > 0) {
    slots.primary = ranged[0];
    if (ranged.length > 1) {
      slots.secondary = ranged[1];
      slots.extraWeapons = ranged.slice(2);
    }
  }

  return slots;
}

export function slotsToItems(slots: KitSlots): KitItemSpec[] {
  const out: KitItemSpec[] = [];
  if (slots.primary) out.push(cloneItem(slots.primary));
  if (slots.secondary) out.push(cloneItem(slots.secondary));
  for (const w of slots.extraWeapons) out.push(cloneItem(w));
  if (slots.helmet) out.push(cloneItem(slots.helmet));
  if (slots.chestplate) out.push(cloneItem(slots.chestplate));
  if (slots.leggings) out.push(cloneItem(slots.leggings));
  if (slots.boots) out.push(cloneItem(slots.boots));
  for (const o of slots.offhand) out.push(cloneItem(o));
  for (const a of slots.ammo) out.push(cloneItem(a));
  for (const f of slots.food) out.push(cloneItem(f));
  for (const p of slots.potions) out.push(cloneItem(p));
  return out;
}

export function applicableEnchantments(
  itemId: string,
  catalog: KitCatalog,
): CatalogEnchantment[] {
  const meta = itemById(catalog, itemId);
  if (!meta) return [];
  return catalog.enchantments.filter((e) => e.categories.includes(meta.category));
}

export function conflictingEnchantments(
  enchantmentId: string,
  existing: Record<string, number> | undefined,
  catalog: KitCatalog,
): string[] {
  const meta = enchantById(catalog, enchantmentId);
  if (!meta || !existing) return [];
  return Object.keys(existing).filter((id) => id !== enchantmentId && meta.conflicts.includes(id));
}

export function canAddEnchantment(
  item: KitItemSpec,
  enchantmentId: string,
  level: number,
  catalog: KitCatalog,
): { ok: boolean; reason?: string } {
  const itemMeta = itemById(catalog, item.item);
  if (!itemMeta) return { ok: false, reason: "Unknown item" };
  const enchMeta = enchantById(catalog, enchantmentId);
  if (!enchMeta) return { ok: false, reason: "Unknown enchantment" };
  if (!enchMeta.categories.includes(itemMeta.category)) {
    return { ok: false, reason: `${enchMeta.label} cannot be applied to ${itemMeta.label}` };
  }
  if (!Number.isInteger(level) || level < 1 || level > enchMeta.max_level) {
    return { ok: false, reason: `${enchMeta.label} level must be 1..${enchMeta.max_level}` };
  }
  const existing = item.enchantments || {};
  const conflicts = conflictingEnchantments(enchantmentId, existing, catalog);
  if (conflicts.length > 0) {
    const labels = conflicts.map((c) => enchantById(catalog, c)?.label ?? c).join(", ");
    return { ok: false, reason: `Conflicts with ${labels}` };
  }
  const alreadyPresent = Object.prototype.hasOwnProperty.call(existing, enchantmentId);
  if (!alreadyPresent && Object.keys(existing).length >= catalog.caps.max_enchantments_per_item) {
    return {
      ok: false,
      reason: `Maximum ${catalog.caps.max_enchantments_per_item} enchantments per item`,
    };
  }
  return { ok: true };
}

export function setItemEnchantment(
  item: KitItemSpec,
  enchantmentId: string,
  level: number | null,
  catalog: KitCatalog,
): KitItemSpec {
  const next = cloneItem(item);
  const current = { ...(next.enchantments || {}) };
  if (level === null) {
    delete current[enchantmentId];
    if (Object.keys(current).length > 0) next.enchantments = current;
    else delete next.enchantments;
    return next;
  }
  const check = canAddEnchantment(next, enchantmentId, level, catalog);
  if (!check.ok) return next;
  current[enchantmentId] = level;
  next.enchantments = current;
  return next;
}

export function consumableCounts(
  items: KitItemSpec[],
  catalog: KitCatalog,
): ConsumableCounts {
  let weapons = 0;
  let arrows = 0;
  let food = 0;
  let goldenApples = 0;
  let potions = 0;
  let slotsUsed = 0;

  for (const entry of items) {
    const meta = itemById(catalog, entry.item);
    if (!meta) continue;
    const count = Math.max(0, entry.count);
    if (meta.group === "melee" || meta.group === "ranged") weapons += count;
    else if (meta.group === "ammo") arrows += count;
    else if (meta.category === "food") food += count;
    else if (meta.category === "golden_apple") goldenApples += count;
    else if (meta.group === "potion") potions += count;
    slotsUsed += Math.ceil(count / meta.max_stack);
  }

  return { weapons, arrows, food, goldenApples, potions, slotsUsed };
}

export function canEditKit(kit: Pick<KitRecord, "builtin">): boolean {
  return !kit.builtin;
}

export function canDeleteKit(kit: Pick<KitRecord, "builtin">): boolean {
  return !kit.builtin;
}

export function validateDraftKit(
  draft: { name: string; description: string; items: KitItemSpec[]; builtin?: boolean },
  catalog: KitCatalog,
): string[] {
  const errors: string[] = [];
  if (draft.builtin) {
    errors.push("Built-in starter classes are read-only; duplicate to customize.");
  }
  const trimmedName = draft.name.trim();
  if (!trimmedName) {
    errors.push("Kit name is required.");
  } else if (trimmedName.length > catalog.caps.max_name_len) {
    errors.push(`Kit name must be at most ${catalog.caps.max_name_len} characters.`);
  } else if (trimmedName.startsWith("/")) {
    errors.push("Kit name must not start with '/'.");
  }
  if (draft.description.trim().length > catalog.caps.max_description_len) {
    errors.push(
      `Kit description must be at most ${catalog.caps.max_description_len} characters.`,
    );
  }
  if (draft.items.length === 0) {
    errors.push("Add at least one item to the kit.");
  }

  const potionIds = new Set(catalog.potions.map((p) => p.id));
  const armorSlots = new Set<string>();
  const offhandCounts: Record<string, number> = {};

  for (const entry of draft.items) {
    const meta = itemById(catalog, entry.item);
    if (!meta) {
      errors.push(`Item '${entry.item}' is not in the survival-legal combat catalog.`);
      continue;
    }
    if (!Number.isInteger(entry.count) || entry.count < 1) {
      errors.push(`${meta.label} count must be at least 1.`);
    }
    if (meta.group === "armor") {
      if (armorSlots.has(meta.category)) {
        errors.push(`Only one ${meta.category} slot is allowed.`);
      }
      armorSlots.add(meta.category);
    }
    if (meta.group === "offhand") {
      offhandCounts[entry.item] = (offhandCounts[entry.item] || 0) + entry.count;
      if (offhandCounts[entry.item] > 1) {
        errors.push(`At most 1 ${meta.label} is allowed.`);
      }
    }
    if (meta.requires_potion && (!entry.potion || !potionIds.has(entry.potion))) {
      errors.push(`${meta.label} requires a valid combat potion effect.`);
    }
    if (entry.enchantments) {
      const keys = Object.keys(entry.enchantments);
      if (keys.length > catalog.caps.max_enchantments_per_item) {
        errors.push(
          `${meta.label} has more than ${catalog.caps.max_enchantments_per_item} enchantments.`,
        );
      }
      for (const [enchId, lvl] of Object.entries(entry.enchantments)) {
        const others = { ...entry.enchantments };
        delete others[enchId];
        const check = canAddEnchantment(
          { ...entry, enchantments: others },
          enchId,
          lvl,
          catalog,
        );
        if (!check.ok) errors.push(`${meta.label}: ${check.reason}`);
      }
    }
  }

  const counts = consumableCounts(draft.items, catalog);
  if (counts.weapons > catalog.caps.max_weapons) {
    errors.push(`At most ${catalog.caps.max_weapons} weapons allowed (have ${counts.weapons}).`);
  }
  if (counts.arrows > catalog.caps.max_arrows) {
    errors.push(`At most ${catalog.caps.max_arrows} arrows allowed (have ${counts.arrows}).`);
  }
  if (counts.food > catalog.caps.max_food) {
    errors.push(`At most ${catalog.caps.max_food} food items allowed (have ${counts.food}).`);
  }
  if (counts.goldenApples > catalog.caps.max_golden_apples) {
    errors.push(
      `At most ${catalog.caps.max_golden_apples} golden apples allowed (have ${counts.goldenApples}).`,
    );
  }
  if (counts.potions > catalog.caps.max_potions) {
    errors.push(`At most ${catalog.caps.max_potions} potions allowed (have ${counts.potions}).`);
  }
  if (counts.slotsUsed > catalog.caps.max_give) {
    errors.push(
      `Kit uses ${counts.slotsUsed} inventory slots (limit ${catalog.caps.max_give}).`,
    );
  }

  return errors;
}

export function estimateKitPower(items: KitItemSpec[], catalog: KitCatalog): KitPower {
  const meleeScores: number[] = [];
  const rangedScores: number[] = [];
  let armor = 0;
  let offhand = 0;
  let enchantments = 0;
  let foodCount = 0;
  let hasGoldenCarrot = false;
  let gappleCount = 0;
  let potionPts = 0;
  let arrowCount = 0;
  let hasSpecialArrow = false;
  let hasInfinityBow = false;
  const itemIds = new Set<string>();

  for (const entry of items) {
    const meta = itemById(catalog, entry.item);
    if (!meta) continue;
    itemIds.add(entry.item);
    const ench = entry.enchantments || {};
    for (const [enchId, lvl] of Object.entries(ench)) {
      const em = enchantById(catalog, enchId);
      if (em) enchantments += em.power_per_level * lvl;
    }
    if (entry.item === "minecraft:bow" && "minecraft:infinity" in ench) {
      hasInfinityBow = true;
    }
    if (meta.group === "melee") meleeScores.push(meta.power_points);
    else if (meta.group === "ranged") rangedScores.push(meta.power_points);
    else if (meta.group === "armor") armor += meta.power_points;
    else if (meta.group === "offhand") offhand += meta.power_points;
    else if (meta.group === "ammo") {
      arrowCount += entry.count;
      if (entry.item === "minecraft:spectral_arrow" || entry.item === "minecraft:tipped_arrow") {
        hasSpecialArrow = true;
      }
    } else if (meta.category === "food") {
      foodCount += entry.count;
      if (entry.item === "minecraft:golden_carrot") hasGoldenCarrot = true;
    } else if (meta.category === "golden_apple") {
      gappleCount += entry.count;
    } else if (meta.group === "potion") {
      const pid = entry.potion || "";
      const perBottle = pid.includes("strong_") || pid.includes("turtle_master") ? 4 : 3;
      potionPts += perBottle * entry.count;
    }
  }

  meleeScores.sort((a, b) => b - a);
  const melee =
    meleeScores.length > 0
      ? meleeScores[0] + meleeScores.slice(1).reduce((s, p) => s + Math.round(p * 0.25), 0)
      : 0;

  rangedScores.sort((a, b) => b - a);
  const rangedBase =
    rangedScores.length > 0
      ? rangedScores[0] + rangedScores.slice(1).reduce((s, p) => s + Math.round(p * 0.25), 0)
      : 0;
  let ranged = 0;
  if (rangedBase > 0) {
    if (arrowCount === 0 && !hasInfinityBow) ranged = Math.round(rangedBase * 0.4);
    else ranged = rangedBase + 2 + (hasSpecialArrow ? 2 : 0);
  }

  const foodPts = Math.min(3, Math.round((foodCount / 16) * 3)) + (hasGoldenCarrot ? 1 : 0);
  const consumables = foodPts + gappleCount * 3 + potionPts;
  const score = melee + ranged + armor + offhand + enchantments + consumables;
  const tier: PowerTier =
    score < 45 ? "Starter" : score < 75 ? "Balanced" : score < 125 ? "Heavy" : "Champion";

  const bot_notes: string[] = [];
  if (itemIds.has("minecraft:crossbow")) {
    bot_notes.push("Bots have limited crossbow support; humans can use it normally.");
  }
  if (itemIds.has("minecraft:mace")) {
    bot_notes.push("Bots have limited mace support (no aerial smash); best for human players.");
  }
  if (itemIds.has("minecraft:totem_of_undying")) {
    bot_notes.push("Totem of Undying triggers automatically in offhand; bots prefer a shield.");
  }
  if (itemIds.has("minecraft:lingering_potion")) {
    bot_notes.push("Bots may not optimally throw lingering potions.");
  }
  if (
    (itemIds.has("minecraft:bow") || itemIds.has("minecraft:crossbow")) &&
    arrowCount === 0 &&
    !hasInfinityBow
  ) {
    bot_notes.push("Ranged weapon included without arrows.");
  }
  if (foodCount === 0 && gappleCount === 0) {
    bot_notes.push("No food included; players cannot regenerate hunger naturally.");
  }

  return {
    score,
    tier,
    breakdown: { melee, ranged, armor, offhand, enchantments, consumables },
    bot_notes,
  };
}

export function powerTierTone(tier: PowerTier): "quiet" | "ready" | "working" | "attention" {
  switch (tier) {
    case "Starter":
      return "quiet";
    case "Balanced":
      return "ready";
    case "Heavy":
      return "working";
    case "Champion":
      return "attention";
  }
}

export function romanNumeral(level: number): string {
  const map: Record<number, string> = { 1: "I", 2: "II", 3: "III", 4: "IV", 5: "V" };
  return map[level] ?? String(level);
}

export function formatEnchantment(
  enchantmentId: string,
  level: number,
  catalog: KitCatalog,
): string {
  const meta = enchantById(catalog, enchantmentId);
  const label = meta?.label ?? titleId(enchantmentId);
  if (meta && meta.max_level === 1 && level === 1) return label;
  return `${label} ${romanNumeral(level)}`;
}
