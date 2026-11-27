import { describe, expect, it } from "vitest";
import {
  DEFAULT_CATALOG,
  type KitItemSpec,
  applicableEnchantments,
  canAddEnchantment,
  canDeleteKit,
  canEditKit,
  conflictingEnchantments,
  consumableCounts,
  estimateKitPower,
  formatEnchantment,
  itemsToSlots,
  powerTierTone,
  romanNumeral,
  setItemEnchantment,
  slotsToItems,
  validateDraftKit,
} from "./kits";

const FIGHTER_ITEMS: KitItemSpec[] = [
  { item: "minecraft:iron_sword", count: 1, enchantments: { "minecraft:sharpness": 2 } },
  { item: "minecraft:iron_helmet", count: 1, enchantments: { "minecraft:protection": 1 } },
  { item: "minecraft:iron_chestplate", count: 1, enchantments: { "minecraft:protection": 1 } },
  { item: "minecraft:iron_leggings", count: 1, enchantments: { "minecraft:protection": 1 } },
  { item: "minecraft:iron_boots", count: 1, enchantments: { "minecraft:protection": 1 } },
  { item: "minecraft:shield", count: 1 },
  { item: "minecraft:cooked_beef", count: 16 },
  { item: "minecraft:golden_apple", count: 2 },
  { item: "minecraft:splash_potion", count: 2, potion: "minecraft:strong_healing" },
];

describe("slot conversion", () => {
  it("round-trips weapons, armor, offhand, ammo, food, and potions cleanly", () => {
    const items: KitItemSpec[] = [
      ...FIGHTER_ITEMS,
      { item: "minecraft:bow", count: 1, enchantments: { "minecraft:power": 2 } },
      { item: "minecraft:trident", count: 1, enchantments: { "minecraft:loyalty": 2 } },
      { item: "minecraft:arrow", count: 32 },
    ];
    const slots = itemsToSlots(items, DEFAULT_CATALOG);
    expect(slots.primary?.item).toBe("minecraft:iron_sword");
    expect(slots.secondary?.item).toBe("minecraft:bow");
    expect(slots.extraWeapons.map((w) => w.item)).toEqual(["minecraft:trident"]);
    expect(slots.helmet?.item).toBe("minecraft:iron_helmet");
    expect(slots.chestplate?.item).toBe("minecraft:iron_chestplate");
    expect(slots.leggings?.item).toBe("minecraft:iron_leggings");
    expect(slots.boots?.item).toBe("minecraft:iron_boots");
    expect(slots.offhand.map((o) => o.item)).toEqual(["minecraft:shield"]);
    expect(slots.ammo).toEqual([{ item: "minecraft:arrow", count: 32 }]);
    expect(slots.food).toHaveLength(2);
    expect(slots.potions).toHaveLength(1);

    const flat = slotsToItems(slots);
    expect(flat).toHaveLength(items.length);
  });

  it("places a ranged weapon into secondary when paired with melee primary", () => {
    const slots = itemsToSlots(
      [
        { item: "minecraft:bow", count: 1 },
        { item: "minecraft:stone_sword", count: 1 },
      ],
      DEFAULT_CATALOG,
    );
    expect(slots.primary?.item).toBe("minecraft:stone_sword");
    expect(slots.secondary?.item).toBe("minecraft:bow");
  });
});

describe("enchantment guardrails", () => {
  it("filters applicable enchantments per item category", () => {
    const swordEnchs = applicableEnchantments("minecraft:iron_sword", DEFAULT_CATALOG).map(
      (e) => e.id,
    );
    expect(swordEnchs).toContain("minecraft:sharpness");
    expect(swordEnchs).toContain("minecraft:knockback");
    expect(swordEnchs).not.toContain("minecraft:power");
    expect(swordEnchs).not.toContain("minecraft:protection");

    const bowEnchs = applicableEnchantments("minecraft:bow", DEFAULT_CATALOG).map((e) => e.id);
    expect(bowEnchs).toContain("minecraft:power");
    expect(bowEnchs).toContain("minecraft:infinity");
    expect(bowEnchs).not.toContain("minecraft:sharpness");

    const bootsEnchs = applicableEnchantments("minecraft:iron_boots", DEFAULT_CATALOG).map(
      (e) => e.id,
    );
    expect(bootsEnchs).toContain("minecraft:feather_falling");
    expect(bootsEnchs).toContain("minecraft:depth_strider");
    expect(bootsEnchs).toContain("minecraft:protection");

    const helmetEnchs = applicableEnchantments("minecraft:iron_helmet", DEFAULT_CATALOG).map(
      (e) => e.id,
    );
    expect(helmetEnchs).not.toContain("minecraft:feather_falling");
  });

  it("detects mutual exclusion conflicts accurately", () => {
    expect(
      conflictingEnchantments(
        "minecraft:smite",
        { "minecraft:sharpness": 3 },
        DEFAULT_CATALOG,
      ),
    ).toEqual(["minecraft:sharpness"]);
    expect(
      conflictingEnchantments(
        "minecraft:fire_protection",
        { "minecraft:protection": 2 },
        DEFAULT_CATALOG,
      ),
    ).toEqual(["minecraft:protection"]);
    expect(
      conflictingEnchantments(
        "minecraft:piercing",
        { "minecraft:multishot": 1 },
        DEFAULT_CATALOG,
      ),
    ).toEqual(["minecraft:multishot"]);
    expect(
      conflictingEnchantments(
        "minecraft:mending",
        { "minecraft:infinity": 1 },
        DEFAULT_CATALOG,
      ),
    ).toEqual(["minecraft:infinity"]);
    expect(
      conflictingEnchantments(
        "minecraft:riptide",
        { "minecraft:loyalty": 2 },
        DEFAULT_CATALOG,
      ),
    ).toEqual(["minecraft:loyalty"]);
  });

  it("enforces max level, conflict groups, and max 4 enchantments per item", () => {
    const sword: KitItemSpec = {
      item: "minecraft:diamond_sword",
      count: 1,
      enchantments: { "minecraft:sharpness": 3 },
    };
    expect(canAddEnchantment(sword, "minecraft:smite", 2, DEFAULT_CATALOG).ok).toBe(false);
    expect(canAddEnchantment(sword, "minecraft:sharpness", 6, DEFAULT_CATALOG).ok).toBe(false);
    expect(canAddEnchantment(sword, "minecraft:sharpness", 0, DEFAULT_CATALOG).ok).toBe(false);
    expect(canAddEnchantment(sword, "minecraft:power", 1, DEFAULT_CATALOG).ok).toBe(false);
    expect(canAddEnchantment(sword, "minecraft:knockback", 2, DEFAULT_CATALOG).ok).toBe(true);

    const fullSword: KitItemSpec = {
      item: "minecraft:diamond_sword",
      count: 1,
      enchantments: {
        "minecraft:sharpness": 5,
        "minecraft:knockback": 2,
        "minecraft:fire_aspect": 2,
        "minecraft:sweeping_edge": 3,
      },
    };
    expect(canAddEnchantment(fullSword, "minecraft:unbreaking", 1, DEFAULT_CATALOG).ok).toBe(
      false,
    );
    // Updating an existing enchantment level is still allowed when 4 are set
    expect(canAddEnchantment(fullSword, "minecraft:sharpness", 4, DEFAULT_CATALOG).ok).toBe(
      true,
    );

    const updated = setItemEnchantment(sword, "minecraft:knockback", 2, DEFAULT_CATALOG);
    expect(updated.enchantments).toEqual({
      "minecraft:sharpness": 3,
      "minecraft:knockback": 2,
    });
    const removed = setItemEnchantment(updated, "minecraft:sharpness", null, DEFAULT_CATALOG);
    expect(removed.enchantments).toEqual({ "minecraft:knockback": 2 });
  });
});

describe("caps, validation, and built-in read-only protection", () => {
  it("computes live cap counters accurately", () => {
    const counts = consumableCounts(FIGHTER_ITEMS, DEFAULT_CATALOG);
    expect(counts).toEqual({
      weapons: 1,
      arrows: 0,
      food: 16,
      goldenApples: 2,
      potions: 2,
      slotsUsed: 10,
    });
  });

  it("flags cap violations, missing potions, and empty names", () => {
    const okErrors = validateDraftKit(
      { name: "Valid Kit", description: "", items: FIGHTER_ITEMS },
      DEFAULT_CATALOG,
    );
    expect(okErrors).toEqual([]);

    const badErrors = validateDraftKit(
      {
        name: "   ",
        description: "",
        items: [
          { item: "minecraft:iron_sword", count: 1 },
          { item: "minecraft:iron_axe", count: 1 },
          { item: "minecraft:bow", count: 1 },
          { item: "minecraft:crossbow", count: 1 },
          { item: "minecraft:trident", count: 1 },
          { item: "minecraft:arrow", count: 129 },
          { item: "minecraft:cooked_beef", count: 65 },
          { item: "minecraft:golden_apple", count: 9 },
          { item: "minecraft:splash_potion", count: 7 },
        ],
      },
      DEFAULT_CATALOG,
    );
    const joined = badErrors.join(" ").toLowerCase();
    expect(joined).toContain("name");
    expect(joined).toContain("weapon");
    expect(joined).toContain("arrow");
    expect(joined).toContain("food");
    expect(joined).toContain("golden apple");
    expect(joined).toContain("potion");
  });

  it("protects built-in starter classes from editing or deletion", () => {
    expect(canEditKit({ builtin: true })).toBe(false);
    expect(canDeleteKit({ builtin: true })).toBe(false);
    expect(canEditKit({ builtin: false })).toBe(true);
    expect(canDeleteKit({ builtin: false })).toBe(true);

    const errors = validateDraftKit(
      { name: "Fighter", description: "", items: FIGHTER_ITEMS, builtin: true },
      DEFAULT_CATALOG,
    );
    expect(errors.some((e) => e.toLowerCase().includes("built-in"))).toBe(true);
  });
});

describe("power tier estimation and formatting", () => {
  it("scores kits into expected tiers with bot notes", () => {
    const fighter = estimateKitPower(FIGHTER_ITEMS, DEFAULT_CATALOG);
    expect(fighter.tier).toBe("Balanced");
    expect(fighter.bot_notes).toEqual([]);
    expect(powerTierTone(fighter.tier)).toBe("ready");

    const starter = estimateKitPower(
      [{ item: "minecraft:wooden_sword", count: 1 }, { item: "minecraft:bread", count: 8 }],
      DEFAULT_CATALOG,
    );
    expect(starter.tier).toBe("Starter");
    expect(powerTierTone(starter.tier)).toBe("quiet");

    const maceCrossbow = estimateKitPower(
      [{ item: "minecraft:mace", count: 1 }, { item: "minecraft:crossbow", count: 1 }],
      DEFAULT_CATALOG,
    );
    expect(maceCrossbow.bot_notes.join(" ").toLowerCase()).toContain("mace");
    expect(maceCrossbow.bot_notes.join(" ").toLowerCase()).toContain("crossbow");
  });

  it("formats enchantments and Roman numerals cleanly", () => {
    expect(romanNumeral(1)).toBe("I");
    expect(romanNumeral(4)).toBe("IV");
    expect(romanNumeral(5)).toBe("V");
    expect(formatEnchantment("minecraft:sharpness", 2, DEFAULT_CATALOG)).toBe("Sharpness II");
    expect(formatEnchantment("minecraft:flame", 1, DEFAULT_CATALOG)).toBe("Flame");
  });
});
