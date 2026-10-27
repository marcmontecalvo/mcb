package com.vulpine.mclab;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import java.util.*;

/** 360-degree local geometry with eye-to-surface occlusion; missing cells are unknown. */
public final class PolicySensor {
    private PolicySensor() {}
    public static List<Map<String,Object>> visible(ServerPlayer p,int tick) {
        var result=new ArrayList<Map<String,Object>>();
        var center=p.blockPosition();
        for(int dx=-8;dx<=8;dx++) for(int dy=-4;dy<=4;dy++) for(int dz=-8;dz<=8;dz++) {
            var pos=center.offset(dx,dy,dz);
            var cell=cell(p,tick,pos,true);
            if(cell!=null) result.add(cell);
        }
        return List.copyOf(result);
    }

    /** Never force-loads a chunk. Null means unavailable or occluded, never air. */
    static Map<String,Object> cell(ServerPlayer p,int tick,BlockPos pos,boolean occluded) {
            // getChunkNow returns only a currently available full chunk, without loading it.
            var chunk=p.level().getChunkSource().getChunkNow(pos.getX()>>4,pos.getZ()>>4);
            if(chunk==null) return null;
            var eye=p.getEyePosition();
            var state=chunk.getBlockState(pos);
            boolean visible=!occluded;
            if(occluded && state.isAir()) {
                var hit=ray(p,eye,Vec3.atCenterOf(pos));
                visible=hit.getType()==HitResult.Type.MISS;
            } else if(occluded) {
                // Test the six actual full-cell surface centers. A block is revealed only
                // when the ray terminates at this cell; intervening occluders stay opaque.
                for(var direction:Direction.values()) {
                    var point=Vec3.atCenterOf(pos).add(direction.getStepX()*.499,
                            direction.getStepY()*.499,direction.getStepZ()*.499);
                    var hit=ray(p,eye,point);
                    if(hit.getType()==HitResult.Type.BLOCK && hit.getBlockPos().equals(pos)) {visible=true;break;}
                }
            }
            if(visible) {
                var properties=new TreeMap<String,String>();
                for(var property:state.getProperties()) addProperty(properties,state,property);
                var boxes=state.getCollisionShape(p.level(),pos).toAabbs().stream()
                        .map(box->List.of(box.minX,box.minY,box.minZ,box.maxX,box.maxY,box.maxZ)).toList();
                return Map.of("position",List.of(pos.getX(),pos.getY(),pos.getZ()),
                        "block",BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString(),
                        "properties",Map.copyOf(properties),"observed_tick",tick,
                        "collision_boxes",boxes,"replaceable",state.canBeReplaced(),
                        "fluid",BuiltInRegistries.FLUID.getKey(state.getFluidState().getType()).toString());
            }
        return null;
    }
    public static List<Map<String,Object>> entities(ServerPlayer p) {
        return entities(p,AwarenessConfig.legacy());
    }
    public static List<Map<String,Object>> entities(ServerPlayer p,AwarenessConfig config) {
        var result=new ArrayList<Map<String,Object>>();
        for(var entity:p.level().getEntities(p,p.getBoundingBox().inflate(config.entityRadius(),config.entityVerticalRadius(),config.entityRadius()))) {
            var point=entity.getBoundingBox().getCenter();
            if(config.entityVisibility().equals("visible") && ray(p,p.getEyePosition(),point).getType()!=HitResult.Type.MISS) continue;
            var entry=new LinkedHashMap<String,Object>();
            entry.put("uuid",entity.getUUID().toString());
            entry.put("type",BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
            entry.put("position",List.of(entity.getX(),entity.getY(),entity.getZ()));
            if(entity instanceof net.minecraft.world.entity.item.ItemEntity item) {
                entry.put("item",BuiltInRegistries.ITEM.getKey(item.getItem().getItem()).toString());
                entry.put("count",item.getItem().getCount());
            }
            result.add(Map.copyOf(entry));
        }
        return List.copyOf(result);
    }
    private static <T extends Comparable<T>> void addProperty(Map<String,String> values, net.minecraft.world.level.block.state.BlockState state, net.minecraft.world.level.block.state.properties.Property<T> property) {
        values.put(property.getName(),property.getName(state.getValue(property)));
    }
    private static BlockHitResult ray(ServerPlayer p,Vec3 from,Vec3 to) {
        // An endpoint can be available while an intervening chunk is absent. The
        // vanilla ray reader must never cause that intervening chunk to load.
        var start=BlockPos.containing(from); var end=BlockPos.containing(to);
        for(int x=Math.min(start.getX()>>4,end.getX()>>4);x<=Math.max(start.getX()>>4,end.getX()>>4);x++)
            for(int z=Math.min(start.getZ()>>4,end.getZ()>>4);z<=Math.max(start.getZ()>>4,end.getZ()>>4);z++)
                if(p.level().getChunkSource().getChunkNow(x,z)==null)
                    return new BlockHitResult(from,Direction.UP,start,false);
        return p.level().clip(new ClipContext(from,to,ClipContext.Block.OUTLINE,ClipContext.Fluid.ANY,p));
    }
}
