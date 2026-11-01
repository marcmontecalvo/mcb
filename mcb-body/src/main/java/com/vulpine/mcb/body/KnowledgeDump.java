package com.vulpine.mcb.body;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.serialization.JsonOps;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.SharedConstants;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderSet;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.item.enchantment.Enchantments;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.FallingBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.level.storage.loot.LootContext;
import net.minecraft.world.level.storage.loot.LootParams;
import net.minecraft.world.level.storage.loot.parameters.LootContextParamSets;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

/**
 * dump_knowledge: writes knowledge/&lt;pack_id&gt;/ per docs/KNOWLEDGE_PACK_FORMAT.md.
 * Runs on the server thread in one tick (the drop simulation dominates; elapsed_ms is reported).
 */
final class KnowledgeDump {
    private static final Gson GSON = new Gson();
    private static final int ROLLS = 20, MANY_ROLLS = 200;
    private static final List<String> TOOL_KINDS = List.of("pickaxe", "axe", "shovel", "hoe", "sword", "shears");
    private static final List<String> DROP_KINDS = List.of("pickaxe", "axe", "shovel", "hoe");
    /** Drop tiers, lowest first (order matters: an omitted tier means "same as the one below"); value is the tool's item prefix. */
    private static final List<Map.Entry<String, String>> TIERS = List.of(
            Map.entry("wood", "wooden"), Map.entry("stone", "stone"), Map.entry("iron", "iron"),
            Map.entry("diamond", "diamond"), Map.entry("netherite", "netherite"));
    private static final Map<String, String> TIER_NAMES = Map.of("wooden", "wood", "golden", "gold");

    private final MinecraftServer server;
    private final ServerLevel level;
    private int dropErrors;

    private final ServerPlayer killer;

    /** killer: the body, used as the player killer when rolling entity loot; null skips mobs.json. */
    KnowledgeDump(MinecraftServer server, ServerPlayer killer) {
        this.server = server; this.level = server.overworld(); this.killer = killer;
    }

    /** Output root: absolute, or relative to the server process directory; no ".." segments. */
    static Path resolveRoot(String dir) {
        if (dir == null || dir.isBlank() || dir.length() > 400 || dir.contains("\0")) throw new IllegalArgumentException("invalid_dir");
        var path = Path.of(dir);
        for (var part : path) if (part.toString().equals("..")) throw new IllegalArgumentException("invalid_dir");
        return path.toAbsolutePath().normalize();
    }

    Map<String, Object> run(Path root) throws IOException {
        long started = System.nanoTime();
        var version = SharedConstants.getCurrentVersion();
        var mods = new ArrayList<String>();
        for (var mod : FabricLoader.getInstance().getAllMods())
            mods.add(mod.getMetadata().getId() + "@" + mod.getMetadata().getVersion().getFriendlyString());
        mods.sort(null);
        var packs = new ArrayList<>(server.getPackRepository().getSelectedIds());
        packs.sort(null);
        var lines = new ArrayList<>(mods);
        lines.addAll(packs);
        String packId = KnowledgeLogic.packId(version.name(), lines);
        Path dir = root.resolve("knowledge").resolve(packId);
        Files.createDirectories(dir);

        var pack = new JsonObject();
        pack.addProperty("schema", 1);
        pack.addProperty("pack_id", packId);
        pack.addProperty("game_version", version.name());
        pack.addProperty("data_version", version.dataVersion().version());
        pack.add("mods", GSON.toJsonTree(mods));
        pack.add("datapacks", GSON.toJsonTree(packs));
        pack.addProperty("created", Instant.now().truncatedTo(ChronoUnit.SECONDS).toString());

        var counts = new LinkedHashMap<String, Object>();
        var blockKinds = mineableKinds();
        write(dir, "items.json", wrap("items", items(counts)));
        write(dir, "blocks.json", wrap("blocks", blocks(blockKinds, counts)));
        write(dir, "tags.json", tags(counts));
        write(dir, "recipes.json", wrap("recipes", recipes(counts)));
        write(dir, "drops.json", drops(blockKinds, counts));
        if (killer != null) write(dir, "mobs.json", mobs(counts));
        else counts.put("mobs", "skipped_no_body");
        write(dir, "pack.json", pack);
        counts.put("drop_errors", dropErrors);
        counts.put("elapsed_ms", (System.nanoTime() - started) / 1_000_000.0);
        var out = new LinkedHashMap<String, Object>();
        out.put("pack_id", packId);
        out.put("path", dir.toString());
        out.put("counts", counts);
        return out;
    }

    private static JsonObject wrap(String key, JsonElement body) {
        var out = new JsonObject();
        out.addProperty("schema", 1);
        out.add(key, body);
        return out;
    }

    private static void write(Path dir, String name, JsonObject json) throws IOException {
        Files.writeString(dir.resolve(name), GSON.toJson(json), StandardCharsets.UTF_8);
    }

    private static String id(Item item) { return BuiltInRegistries.ITEM.getKey(item).toString(); }
    private static String id(Block block) { return BuiltInRegistries.BLOCK.getKey(block).toString(); }

    // ---- items
    private JsonObject items(Map<String, Object> counts) {
        var items = new JsonObject();
        var context = new LootContext.Builder(new LootParams.Builder(level).create(LootContextParamSets.EMPTY)).create(Optional.empty());
        for (var item : sorted(BuiltInRegistries.ITEM, KnowledgeDump::id)) {
            if (item == Items.AIR) continue;
            var stack = new ItemStack(item);
            var entry = new JsonObject();
            entry.addProperty("stack", stack.getMaxStackSize());
            if (stack.isDamageableItem()) entry.addProperty("durability", stack.getMaxDamage());
            var tool = toolInfo(item, stack);
            if (tool != null) entry.add("tool", tool);
            var fuel = stack.get(DataComponents.COOKING_FUEL);
            if (fuel != null) {
                try {
                    int ticks = fuel.burnTime().get(context, 0);
                    if (ticks > 0) entry.addProperty("fuel_ticks", ticks);
                } catch (RuntimeException ignored) { /* unresolvable burn time: not listed as fuel */ }
            }
            items.add(id(item), entry);
        }
        counts.put("items", items.size());
        return items;
    }

    private static JsonObject toolInfo(Item item, ItemStack stack) {
        var tool = stack.get(DataComponents.TOOL);
        if (tool == null) return null;
        String path = BuiltInRegistries.ITEM.getKey(item).getPath();
        for (var kind : TOOL_KINDS) {
            if (!path.equals(kind) && !path.endsWith("_" + kind)) continue;
            String prefix = path.equals(kind) ? "none" : path.substring(0, path.length() - kind.length() - 1);
            float speed = tool.defaultMiningSpeed();
            for (var rule : tool.rules()) if (rule.speed().isPresent()) speed = Math.max(speed, rule.speed().get());
            var out = new JsonObject();
            out.addProperty("kind", kind);
            out.addProperty("tier", TIER_NAMES.getOrDefault(prefix, prefix));
            out.addProperty("speed", speed);
            return out;
        }
        return null;
    }

    // ---- blocks
    /** Block id to tool kinds, from the minecraft:mineable/* tags. */
    private static Map<Block, List<String>> mineableKinds() {
        var out = new java.util.HashMap<Block, List<String>>();
        BuiltInRegistries.BLOCK.getTags().forEach(tag -> {
            var key = tag.key().location();
            if (!key.getPath().startsWith("mineable/")) return;
            String kind = key.getPath().substring("mineable/".length());
            tag.stream().forEach(holder -> out.computeIfAbsent(holder.value(), b -> new ArrayList<>()).add(kind));
        });
        out.values().forEach(list -> list.sort(null));
        return out;
    }

    private JsonObject blocks(Map<Block, List<String>> kinds, Map<String, Object> counts) {
        var blocks = new JsonObject();
        for (var block : sorted(BuiltInRegistries.BLOCK, KnowledgeDump::id)) {
            BlockState state = block.defaultBlockState();
            var entry = new JsonObject();
            var asItem = block.asItem();
            if (asItem == Items.AIR) entry.add("item", com.google.gson.JsonNull.INSTANCE);
            else entry.addProperty("item", id(asItem));
            entry.addProperty("hardness", block.defaultDestroyTime());
            entry.addProperty("needs_correct_tool", state.requiresCorrectToolForDrops());
            entry.add("mineable", GSON.toJsonTree(kinds.getOrDefault(block, List.of())));
            entry.addProperty("gravity", block instanceof FallingBlock);
            String support = KnowledgeLogic.needsSupport(classChain(block));
            if (support == null) entry.add("needs_support", com.google.gson.JsonNull.INSTANCE);
            else entry.addProperty("needs_support", support);
            var properties = new JsonObject();
            for (var property : block.getStateDefinition().getProperties()) properties.add(property.getName(), values(property));
            entry.add("properties", properties);
            blocks.add(id(block), entry);
        }
        counts.put("blocks", blocks.size());
        return blocks;
    }

    private static List<String> classChain(Block block) {
        var names = new ArrayList<String>();
        for (Class<?> c = block.getClass(); c != null && c != Object.class; c = c.getSuperclass()) names.add(c.getSimpleName());
        return names;
    }

    private static <T extends Comparable<T>> JsonArray values(Property<T> property) {
        var out = new JsonArray();
        for (T value : property.getPossibleValues()) out.add(property.getName(value));
        return out;
    }

    // ---- tags
    private JsonObject tags(Map<String, Object> counts) {
        var out = new JsonObject();
        out.addProperty("schema", 1);
        out.add("items", tagTable(BuiltInRegistries.ITEM.getTags(), holder -> id(holder.value())));
        out.add("blocks", tagTable(BuiltInRegistries.BLOCK.getTags(), holder -> id(holder.value())));
        counts.put("tags", out.getAsJsonObject("items").size() + out.getAsJsonObject("blocks").size());
        return out;
    }

    private static <T> JsonObject tagTable(java.util.stream.Stream<HolderSet.Named<T>> tags,
                                           java.util.function.Function<Holder<T>, String> name) {
        var sorted = new TreeMap<String, List<String>>();
        tags.forEach(tag -> sorted.put("#" + tag.key().location(),
                tag.stream().map(name).sorted().toList()));
        var out = new JsonObject();
        sorted.forEach((key, members) -> out.add(key, GSON.toJsonTree(members)));
        return out;
    }

    // ---- recipes
    private JsonArray recipes(Map<String, Object> counts) {
        var ops = server.registryAccess().createSerializationContext(JsonOps.INSTANCE);
        var out = new JsonArray();
        var types = new TreeMap<String, Integer>();
        int raw = 0;
        var holders = new ArrayList<>(server.getRecipeManager().getRecipes());
        holders.sort(Comparator.comparing(h -> h.id().identifier().toString()));
        for (var holder : holders) {
            String recipeId = holder.id().identifier().toString();
            JsonObject encoded;
            try {
                encoded = Recipe.DIRECT_CODEC.encodeStart(ops, holder.value()).getOrThrow().getAsJsonObject();
            } catch (RuntimeException error) {
                encoded = new JsonObject();
                encoded.addProperty("type", BuiltInRegistries.RECIPE_SERIALIZER.getKey(holder.value().getSerializer()).toString());
                encoded.addProperty("encode_error", String.valueOf(error.getMessage()));
            }
            var parsed = KnowledgeLogic.recipe(recipeId, encoded);
            if (parsed.get("station").isJsonNull()) raw++;
            types.merge(parsed.get("type").getAsString(), 1, Integer::sum);
            out.add(parsed);
        }
        counts.put("recipes", out.size());
        counts.put("recipes_raw", raw);
        counts.put("recipe_types", types);
        return out;
    }

    // ---- drops
    private JsonObject drops(Map<Block, List<String>> kinds, Map<String, Object> counts) {
        var silk = new ItemStack(Items.NETHERITE_PICKAXE);
        silk.enchant(server.registryAccess().lookupOrThrow(Registries.ENCHANTMENT).getOrThrow(Enchantments.SILK_TOUCH), 1);
        var table = new JsonObject();
        for (var block : sorted(BuiltInRegistries.BLOCK, KnowledgeDump::id)) {
            BlockState state = block.defaultBlockState();
            String kind = kinds.getOrDefault(block, List.of()).stream().filter(DROP_KINDS::contains).findFirst().orElse(null);
            var entry = new JsonObject();
            var previous = roll(state, ItemStack.EMPTY);
            entry.add("hand", previous);
            if (kind != null) {
                for (var tier : TIERS) {
                    var tool = new ItemStack(BuiltInRegistries.ITEM.getValue(Identifier.parse("minecraft:" + tier.getValue() + "_" + kind)));
                    var current = roll(state, tool);
                    if (!current.equals(previous)) entry.add(tier.getKey(), current);
                    previous = current;
                }
            }
            var silkDrops = roll(state, silk);
            if (!silkDrops.equals(previous)) entry.add("silk_touch", silkDrops);
            boolean any = block.asItem() != Items.AIR;
            for (var key : entry.keySet()) if (entry.getAsJsonArray(key).size() > 0) any = true;
            if (any) table.add(id(block), entry);
        }
        counts.put("drops", table.size());
        var out = new JsonObject();
        out.addProperty("schema", 1);
        out.add("tools", GSON.toJsonTree(List.of("hand", "wood", "stone", "iron", "diamond", "netherite", "silk_touch")));
        out.add("drops", table);
        return out;
    }

    /** Roll the block's loot table with this tool: 20 rolls, 200 when they vary (K5). */
    private JsonArray roll(BlockState state, ItemStack tool) {
        if (state.requiresCorrectToolForDrops() && !tool.isCorrectToolForDrops(state)) return new JsonArray();
        try {
            var rolls = new ArrayList<Map<String, Integer>>();
            for (int i = 0; i < ROLLS; i++) rolls.add(blockRoll(state, tool));
            if (KnowledgeLogic.varies(rolls)) for (int i = ROLLS; i < MANY_ROLLS; i++) rolls.add(blockRoll(state, tool));
            return KnowledgeLogic.summarize(rolls);
        } catch (RuntimeException error) {
            dropErrors++;
            return new JsonArray();
        }
    }

    private Map<String, Integer> blockRoll(BlockState state, ItemStack tool) {
        var perRoll = new TreeMap<String, Integer>();
        for (var drop : Block.getDrops(state, level, BlockPos.ZERO, null, null, tool))
            if (!drop.isEmpty()) perRoll.merge(id(drop.getItem()), drop.getCount(), Integer::sum);
        return perRoll;
    }

    // ---- mobs (K7)
    /** Hand-checked vanilla interactions: entity type to [item, gives, min, max, per_color]. */
    private static final Map<String, List<Object[]>> INTERACTIONS = Map.of(
            "minecraft:cow", List.<Object[]>of(new Object[]{"minecraft:bucket", "minecraft:milk_bucket", 1, 1, false}),
            "minecraft:mooshroom", List.<Object[]>of(
                    new Object[]{"minecraft:bucket", "minecraft:milk_bucket", 1, 1, false},
                    new Object[]{"minecraft:bowl", "minecraft:mushroom_stew", 1, 1, false},
                    new Object[]{"minecraft:shears", "minecraft:red_mushroom", 5, 5, false}),
            "minecraft:sheep", List.<Object[]>of(new Object[]{"minecraft:shears", "minecraft:white_wool", 1, 3, true}));

    private JsonObject mobs(Map<String, Object> counts) {
        var table = new JsonObject();
        for (var type : sorted(BuiltInRegistries.ENTITY_TYPE, t -> BuiltInRegistries.ENTITY_TYPE.getKey(t).toString())) {
            net.minecraft.world.entity.Entity entity;
            try { entity = type.create(level, net.minecraft.world.entity.EntitySpawnReason.COMMAND); }
            catch (RuntimeException error) { continue; }
            if (!(entity instanceof net.minecraft.world.entity.Mob mob)) continue;
            String name = BuiltInRegistries.ENTITY_TYPE.getKey(type).toString();
            boolean passive = mob instanceof net.minecraft.world.entity.animal.Animal;
            var entry = new JsonObject();
            entry.addProperty("category", type.getCategory().getName());
            entry.addProperty("passive", passive);
            entry.addProperty("health", mob.getMaxHealth());
            var drops = new JsonArray();
            try { drops = mobLoot(mob); } catch (RuntimeException error) { dropErrors++; }
            entry.add("killed_by_player", drops);
            var interactions = new JsonArray();
            for (var i : INTERACTIONS.getOrDefault(name, List.of())) {
                var o = new JsonObject();
                o.addProperty("item", (String) i[0]);
                o.addProperty("gives", (String) i[1]);
                o.addProperty("min", (Integer) i[2]);
                o.addProperty("max", (Integer) i[3]);
                if ((Boolean) i[4]) o.addProperty("per_color", true);
                interactions.add(o);
            }
            entry.add("interactions", interactions);
            if (drops.size() > 0 || passive) table.add(name, entry);
        }
        counts.put("mobs", table.size());
        return wrap("mobs", table);
    }

    /** Roll the entity's loot table with a player killer (200 rolls). */
    private JsonArray mobLoot(net.minecraft.world.entity.Mob mob) {
        var key = mob.getLootTable();
        if (key.isEmpty()) return new JsonArray();
        var lootTable = server.reloadableRegistries().getLootTable(key.get());
        var rolls = new ArrayList<Map<String, Integer>>();
        for (int i = 0; i < MANY_ROLLS; i++) {
            var params = new LootParams.Builder(level)
                    .withParameter(net.minecraft.world.level.storage.loot.parameters.LootContextParams.THIS_ENTITY, mob)
                    .withParameter(net.minecraft.world.level.storage.loot.parameters.LootContextParams.ORIGIN, mob.position())
                    .withParameter(net.minecraft.world.level.storage.loot.parameters.LootContextParams.DAMAGE_SOURCE, level.damageSources().playerAttack(killer))
                    .withOptionalParameter(net.minecraft.world.level.storage.loot.parameters.LootContextParams.ATTACKING_ENTITY, killer)
                    .withOptionalParameter(net.minecraft.world.level.storage.loot.parameters.LootContextParams.DIRECT_ATTACKING_ENTITY, killer)
                    .withParameter(net.minecraft.world.level.storage.loot.parameters.LootContextParams.LAST_DAMAGE_PLAYER, killer)
                    .create(LootContextParamSets.ENTITY);
            var perRoll = new TreeMap<String, Integer>();
            for (var drop : lootTable.getRandomItems(params)) if (!drop.isEmpty()) perRoll.merge(id(drop.getItem()), drop.getCount(), Integer::sum);
            rolls.add(perRoll);
        }
        return KnowledgeLogic.summarize(rolls);
    }

    private static <T> List<T> sorted(Iterable<T> registry, java.util.function.Function<T, String> key) {
        var list = new ArrayList<T>();
        registry.forEach(list::add);
        list.sort(Comparator.comparing(key));
        return list;
    }
}
