package com.vulpine.mcb.body;

import com.google.gson.JsonParser;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Native routing safety and state-machine tests; no server or external client. */
public final class RouteMotorTest {
    private record Key(int x, int y, int z) { }
    private static int checks;
    private static void check(boolean yes, String label) {
        checks++; if (!yes) throw new AssertionError(label);
    }
    private static Map<Key, Map<String,Object>> floor() {
        var cells = new HashMap<Key,Map<String,Object>>();
        for (int x=-3;x<=8;x++) for(int z=-3;z<=8;z++) for(int y=-3;y<=6;y++)
            cells.put(new Key(x,y,z),Map.of("block",y==0?"minecraft:stone":"minecraft:air",
                    "collision_boxes",y==0?List.of(List.of(0,0,0,1,1,1)):List.of(),"fluid","minecraft:empty"));
        return cells;
    }
    private static RouteMotor motor(List<RouteMotor.Point> points) {
        return new RouteMotor("test",points,new RouteMotor.Policy(true,true,true,3));
    }
    private static RouteMotor.Pose pose(double x,double y,double z,double yaw) {
        return new RouteMotor.Pose(x,y,z,0,0,yaw,true,20);
    }
    private static RouteGeometry geometry(Map<Key,Map<String,Object>> cells) {
        return new RouteGeometry((x,y,z)->cells.get(new Key(x,y,z)));
    }
    private static void reject(Runnable action,String label) {
        try { action.run(); throw new AssertionError(label); }
        catch (IllegalArgumentException expected) { checks++; }
    }
    private static Map<String,Object> water(String level) {
        return Map.of("block","minecraft:water","fluid","minecraft:water","properties",Map.of("level",level),"collision_boxes",List.of());
    }
    private static Map<String,Object> stone() {
        return Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty");
    }
    private static final RouteMotor.Policy SWIM=new RouteMotor.Policy(true,true,true,3,true,false);
    private static RouteMotor.Pose wet(double x,double y,double z) {
        return new RouteMotor.Pose(x,y,z,0,0,-90,false,20,true);
    }
    /** Pool at x=2..4, z=0: water at y=0 over a stone bed at y=-1; bank stone at (5,1,0). */
    private static Map<Key,Map<String,Object>> pool() {
        var cells=floor();
        for(int x=2;x<=4;x++) { cells.put(new Key(x,0,0),water("0")); cells.put(new Key(x,-1,0),stone()); }
        cells.put(new Key(5,1,0),stone());
        return cells;
    }
    /** Stone box x,z in -3..3, y 0..6; water at the listed cells, air at the others listed. */
    private static Map<Key,Map<String,Object>> box() {
        var cells=new HashMap<Key,Map<String,Object>>();
        for (int x=-3;x<=3;x++) for(int z=-3;z<=3;z++) for(int y=0;y<=6;y++) cells.put(new Key(x,y,z),stone());
        return cells;
    }
    private static final Map<String,Object> AIR=Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty");
    private static void surfaceChecks() {
        var diver=new RouteMotor.Pose(.5,1,.5,0,0,0,false,20,true);
        // Open column: water at (0,1..3,0), air at (0,4,0).
        var open=box();
        for(int y=1;y<=3;y++) open.put(new Key(0,y,0),water("0"));
        open.put(new Key(0,4,0),AIR);
        var up=RouteMotor.surface(diver,2.5,geometry(open));
        check(up.jump() && up.forward()==0 && !up.sneak(),"head under an open column: jump only");
        // Solid ceiling over (0,3,0); water at (1,1..2,0), air at (1,3,0). (-1,1..2,0) unknown, air above it.
        var ceiling=box();
        for(int y=1;y<=2;y++) { ceiling.put(new Key(0,y,0),water("0")); ceiling.put(new Key(1,y,0),water("0")); ceiling.remove(new Key(-1,y,0)); }
        ceiling.put(new Key(1,3,0),AIR); ceiling.put(new Key(-1,3,0),AIR);
        var side=RouteMotor.surface(diver,2.5,geometry(ceiling));
        check(side.forward()>0 && Math.abs(side.yaw()+90)<1e-6 && side.jump(),"under a ceiling: steer toward the open water beside it (+x), never into unknown cells");
        // Without the known side, only the unknown remains: no path, fall back to jump.
        ceiling.put(new Key(1,1,0),stone()); ceiling.put(new Key(1,2,0),stone());
        var unknownOnly=RouteMotor.surface(diver,2.5,geometry(ceiling));
        check(unknownOnly.jump() && unknownOnly.forward()==0,"an unknown cell is never a path cell");
        // Sealed pocket.
        var sealed=box();
        for(int y=1;y<=2;y++) sealed.put(new Key(0,y,0),water("0"));
        var stuck=RouteMotor.surface(diver,2.5,geometry(sealed));
        check(stuck.jump() && stuck.forward()==0,"sealed pocket with no air: fall back to jump");
        // The way out lies below a lip: down, across, up.
        var lip=box();
        lip.put(new Key(0,2,0),water("0")); lip.put(new Key(0,1,0),water("0"));
        lip.put(new Key(1,1,0),water("0")); lip.put(new Key(1,0,0),water("0")); lip.put(new Key(0,0,0),water("0"));
        lip.put(new Key(2,1,0),water("0")); lip.put(new Key(2,0,0),water("0")); lip.put(new Key(2,2,0),AIR);
        var dive=RouteMotor.surface(new RouteMotor.Pose(.5,2,.5,0,0,0,false,20,true),2.5,geometry(lip));
        check(dive.sneak() && !dive.jump(),"a way out below: swim down");
        // Air pocket beside the shaft under a roof: air at (1,1..2,0), stone at (1,3,0). A body floating
        // with feet at 1.7 (head cell 2) would hit the roof: sink first, then walk in without jumping.
        var nook=box();
        for(int y=1;y<=3;y++) nook.put(new Key(0,y,0),water("0"));
        nook.put(new Key(1,1,0),AIR); nook.put(new Key(1,2,0),AIR); nook.put(new Key(0,4,0),stone());
        var high=RouteMotor.surface(new RouteMotor.Pose(.5,1.7,.5,0,0,0,false,20,true),2.97,geometry(nook));
        check(high.sneak() && !high.jump() && high.forward()==0,"pocket under a roof, body too high: sink first");
        var low=RouteMotor.surface(new RouteMotor.Pose(.5,1,.5,0,0,0,false,20,true),2.6,geometry(nook));
        check(low.forward()>0 && !low.jump() && !low.sneak(),"pocket under a roof, body low enough: walk in without jumping");
        // Run 20261116T172715478561Z: centre column x=1 is open to air but the 0.6-wide body at x=1.02
        // overlaps x=0, whose stone at y=3 pins the head. Jumping straight up drowned it: steer to x=2.
        var edge=box();
        for(int y=1;y<=2;y++) edge.put(new Key(0,y,0),water("0"));
        for(int x=1;x<=2;x++) { for(int y=1;y<=3;y++) edge.put(new Key(x,y,0),water("0")); edge.put(new Key(x,4,0),AIR); }
        var pinned=new RouteMotor.Pose(1.02,1.2,.5,0,0,0,false,20,true);
        var wedge=RouteMotor.surface(pinned,2.82,geometry(edge));
        check(wedge.forward()>0 && Math.abs(wedge.yaw()+90)<1e-6,"body overlapping a roofed column steers sideways, not straight up");
        check(!geometry(edge).openAbove(pinned,new RouteMotor.Point(1,3,0)),"openAbove tests the whole hitbox");
        check(geometry(edge).openAbove(new RouteMotor.Pose(2.5,1.2,.5,0,0,0,false,20,true),new RouteMotor.Point(2,3,0)),"openAbove true when the box fits");
    }
    private static void swimAndLandingChecks() {
        var channel=floor();
        for (int x=0;x<=2;x++) channel.put(new Key(x,1,0),water("0"));
        channel.put(new Key(3,1,0),stone());
        var path=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),
                new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,2,0));
        var traverse=motor(path);
        traverse.tick(wet(.5,1.04,.5),geometry(channel));
        traverse.tick(wet(1.5,1.04,.5),geometry(channel));
        check(traverse.status().get("next_index").equals(2),"ungrounded wading advances the first intermediate waypoint");
        traverse.tick(wet(2.5,1.04,.5),geometry(channel));
        check(traverse.status().get("next_index").equals(3),"ungrounded wading advances to the bank rather than oscillating");
        traverse.tick(pose(3.5,2,.5,-90),geometry(channel));
        check(traverse.status().get("state").equals("done"),"multi-waypoint wading completes without swimming");
        var skipped=motor(path);
        skipped.tick(wet(.5,1.04,.5),geometry(channel));
        skipped.tick(wet(2.5,1.04,.5),geometry(channel));
        check(skipped.status().get("next_index").equals(3),"wet supported overshoot resynchronizes to a later waypoint");
        var shallow=floor();
        shallow.put(new Key(0,1,0),water("0"));
        shallow.put(new Key(1,1,0),stone());
        var wader=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,2,0)));
        var rise=wader.tick(wet(.7,1.04,.5),geometry(shallow));
        check(wader.active() && rise.jump() && rise.forward()>0,"wading bank exit holds jump off the bottom without swimming enabled");
        wader.tick(pose(1.5,2,.5,-90),geometry(shallow));
        check(wader.status().get("state").equals("done"),"wading bank exit completes");
        // Wading onto a slab under a low roof: a 0.5 rise needs the planner's target+1.8, not jump headroom.
        var slabRoof=floor();
        slabRoof.put(new Key(0,1,0),water("0"));
        slabRoof.put(new Key(1,1,0),Map.of("block","minecraft:stone_slab","fluid","minecraft:empty",
                "collision_boxes",List.of(List.of(0,0,0,1,.5,1))));
        for(int x=-1;x<=2;x++) for(int z=-1;z<=1;z++) slabRoof.put(new Key(x,4,z),stone());
        var slabRoute=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1.5,0));
        var footed=motor(slabRoute).tick(new RouteMotor.Pose(.7,1,.5,0,0,-90,true,20,true),geometry(slabRoof));
        check(footed.forward()>0 && footed.jump(),"grounded wader steps onto a roofed slab");
        var buoyed=motor(slabRoute);
        var lifted=buoyed.tick(wet(.7,1.04,.5),geometry(slabRoof));
        check(buoyed.active() && lifted.forward()>0 && lifted.jump(),"buoyant wader keeps moving onto a roofed slab");
        var g=geometry(pool());
        check(g.swimSurface(new RouteMotor.Point(3,1,0)),"known still water is a surface stance");
        check(!g.swimSurface(new RouteMotor.Point(1,1,0)),"land is not a surface stance");
        check(!g.standable(new RouteMotor.Point(3,1,0)),"water is never land support");
        for (var plant : List.of("minecraft:seagrass", "minecraft:tall_seagrass")) {
            var planted = pool();
            for (int x=2; x<=4; x++) planted.put(new Key(x,0,0),Map.of(
                    "block",plant,"fluid","minecraft:water","properties",Map.of(),"collision_boxes",List.of()));
            var pg = geometry(planted);
            check(pg.swimSurface(new RouteMotor.Point(3,1,0)),"aquatic plants retain source-water surface support");
            var swimmer = new RouteMotor("plant", List.of(new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0)), SWIM);
            var input = swimmer.tick(wet(2.5,0,.5),pg);
            check(swimmer.active() && input.jump(),"submerged body rises through aquatic source plants");
            var rising = swimmer.tick(wet(3.5,0,.5),pg);
            check(swimmer.active() && rising.jump(),"last swim waypoint keeps rising instead of stopping submerged");
            swimmer.tick(wet(3.5,1,.5),pg);
            check(swimmer.status().get("state").equals("done"),"plant-water route completes");
        }
        Map<String,Object> bubble=Map.of("block","minecraft:bubble_column","fluid","minecraft:water","properties",Map.of("drag","false"),"collision_boxes",List.of());
        Map<String,Object> lava=Map.of("block","minecraft:lava","fluid","minecraft:lava","properties",Map.of("level","0"),"collision_boxes",List.of());
        for(var bad:List.of(water("1"),water("8"),bubble,lava)) {
            var variant=pool(); variant.put(new Key(3,0,0),bad);
            check(!geometry(variant).swimSurface(new RouteMotor.Point(3,1,0)),"flowing/bubble/lava never a stance");
        }
        var unknown=pool(); unknown.remove(new Key(3,0,0));
        check(!geometry(unknown).swimSurface(new RouteMotor.Point(3,1,0)),"unknown water is not support");
        var waterlogged=pool(); waterlogged.put(new Key(3,0,0),Map.of("block","minecraft:oak_stairs","fluid","minecraft:water","properties",Map.of("level","0"),"collision_boxes",List.of(List.of(0,0,0,1,.5,1))));
        check(!geometry(waterlogged).swimSurface(new RouteMotor.Point(3,1,0)),"waterlogged block is not still water");
        var roofed=pool(); roofed.put(new Key(3,2,0),stone());
        check(!geometry(roofed).swimSurface(new RouteMotor.Point(3,1,0)),"no headroom, no stance");
        // Enter from land, swim along the surface, exit one block up.
        var route=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),
                new RouteMotor.Point(4,1,0),new RouteMotor.Point(5,2,0));
        var motor=new RouteMotor("swim",route,SWIM);
        var enter=motor.tick(pose(1.5,1,.5,-90),g);
        check(motor.active() && enter.forward()>0 && !enter.jump(),"walks into the water without jumping");
        var surface=motor.tick(wet(2.5,.3,.5),g);
        check(motor.active() && surface.jump() && surface.forward()>0 && surface.sprint(),"holds jump and sprint-swims along the surface");
        check((int)motor.status().get("next_index")==2,"swim arrival advances");
        motor.tick(wet(3.5,.3,.5),g);
        var exit=motor.tick(wet(4.5,.3,.5),g);
        check(motor.active() && exit.jump() && !exit.sprint(),"jumps out onto a +1 bank without sprint");
        motor.tick(pose(5.5,2,.5,-90),g);
        check(motor.status().get("state").equals("done"),"exit onto land completes");
        // A failed exit hop leaves the water every bob; airborne ticks must not reset the stall count.
        var bob=new RouteMotor("bob",route.subList(3,5),SWIM);
        for(int t=0;t<80 && bob.active();t++)
            bob.tick(t%20<10 ? wet(4.7,.3,.5) : new RouteMotor.Pose(4.7,1.5,.5,0,0,-90,false,20,false),g);
        check(!bob.active() && bob.status().get("reason").equals("route_no_progress"),"endless exit bobbing gives up");
        check(RouteMotor.levelPitch(83.65)==-45 && RouteMotor.levelPitch(38.65)==-38.65
                && RouteMotor.levelPitch(-10)==10 && RouteMotor.levelPitch(0)==0,"route following levels the view");
        var noSprint=new RouteMotor("s",route,new RouteMotor.Policy(true,false,true,3,true,false));
        noSprint.tick(pose(1.5,1,.5,-90),g);
        check(!noSprint.tick(wet(2.5,.3,.5),g).sprint(),"sprint policy off");
        var landOnly=new RouteMotor("l",route.subList(0,3),new RouteMotor.Policy(true,true,true,3));
        landOnly.tick(pose(1.5,1,.5,-90),g);
        check(!landOnly.active(),"water target refused without swim policy");
        reject(()->new RouteMotor("l",List.of(route.get(0),new RouteMotor.Point(2,-3,0)),new RouteMotor.Policy(true,true,true,3)),"deep drop refused without swim policy");
        var flowing=pool(); flowing.put(new Key(2,0,0),water("1"));
        var refused=new RouteMotor("f",route,SWIM); refused.tick(pose(1.5,1,.5,-90),geometry(flowing));
        check(!refused.active() && refused.status().get("reason").equals("route_target_unknown_or_changed"),"flowing target refused");
        // Water landings: a 4-block drop is legal only into still water over a known floor.
        var drop=List.of(new RouteMotor.Point(1,5,0),new RouteMotor.Point(2,1,0));
        var landing=new RouteMotor("d",drop,SWIM);
        var fall=landing.tick(pose(1.5,5,.5,-90),g);
        check(landing.active() && fall.forward()>0,"water landing admitted");
        check(g.waterLanding(new RouteMotor.Point(2,1,0)),"known floor under still water");
        var deep=pool(); deep.put(new Key(2,-1,0),water("0")); deep.put(new Key(2,-2,0),stone());
        check(geometry(deep).waterLanding(new RouteMotor.Point(2,1,0)),"deeper pool still lands");
        var noFloor=pool(); noFloor.remove(new Key(2,-1,0));
        check(!geometry(noFloor).waterLanding(new RouteMotor.Point(2,1,0)),"unknown floor refuses landing");
        var airFloor=pool(); airFloor.put(new Key(2,-1,0),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty"));
        check(!geometry(airFloor).waterLanding(new RouteMotor.Point(2,1,0)),"air floor refuses landing");
        var dry=new RouteMotor("d",drop,SWIM); dry.tick(pose(1.5,5,.5,-90),geometry(noFloor));
        check(!dry.active() && dry.status().get("reason").equals("route_drop_requires_water"),"unsafe landing refused");
        var ground=new RouteMotor("d",drop,SWIM); ground.tick(pose(1.5,5,.5,-90),geometry(floor()));
        check(!ground.active() && ground.status().get("reason").equals("route_drop_requires_water"),"a 4-block drop onto ground is still refused");
    }
    private static void sprintAscentChecks() {
        var cells=floor();
        cells.put(new Key(3,1,0),stone()); cells.put(new Key(4,1,0),stone());
        var g=geometry(cells);
        var route=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(4,2,0));
        var fast=new RouteMotor.Pose(2.0,1,.5,.25,0,-90,true,20);
        var jump=motor(route).tick(fast,g);
        check(jump.jump() && jump.sprint() && jump.forward()==1,"straight traverse into an ascent keeps sprinting and jumps a stride early");
        var slow=motor(route).tick(new RouteMotor.Pose(2.0,1,.5,0,0,-90,true,20),g);
        check(!slow.jump(),"standing start keeps the short jump range");
        var run=motor(route).tick(new RouteMotor.Pose(1.2,1,.5,.25,0,-90,true,20),g);
        check(run.sprint() && !run.jump(),"sprint continues through the traverse before the ascent");
        var porch=floor(); porch.putAll(cells); porch.put(new Key(3,4,0),stone()); porch.put(new Key(4,4,0),stone());
        check(motor(route).tick(fast,geometry(porch)).jump(),"jumps up into a roofed 2-high doorway");
        var walking=new RouteMotor("w",route,new RouteMotor.Policy(true,false,true,3));
        check(!walking.tick(fast,g).sprint(),"sprint policy off");
        var bend=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(3,2,1));
        check(!motor(bend).tick(fast,g).sprint(),"ascent that turns afterwards does not sprint");
        check(!motor(route).tick(new RouteMotor.Pose(2.0,1,.5,.25,0,-90,true,6),g).sprint(),"food gate on sprint ascent");
        var capped=floor(); capped.put(new Key(3,1,0),stone()); capped.put(new Key(4,1,0),stone()); capped.put(new Key(3,3,0),stone());
        var lowCeiling=motor(route).tick(fast,geometry(capped));
        check(!lowCeiling.sprint(),"low ceiling over the step cancels the sprint ascent");
    }
    private static void climbChecks() {
        var cells=floor();
        for(int y=1;y<=4;y++) cells.put(new Key(3,y,0),Map.of("block","minecraft:ladder","fluid","minecraft:empty",
                "properties",Map.of("facing","south"),"collision_boxes",List.of(List.of(0,0,0,1,1,.1875))));
        var g=geometry(cells);
        var policy=new RouteMotor.Policy(true,true,true,3,false,true);
        var up=List.of(new RouteMotor.Point(3,1,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(3,3,0));
        reject(()->new RouteMotor("c",up,new RouteMotor.Policy(true,true,true,3)),"vertical edge needs the climb policy");
        reject(()->new RouteMotor("c",List.of(up.get(0),up.get(2)),policy),"climb is one block per edge");
        check(g.climbable(up.get(1)),"ladder cell is climbable");
        check(!geometry(floor()).climbable(up.get(1)),"air is not climbable");
        var headless=floor(); headless.putAll(cells); headless.remove(new Key(3,3,0));
        check(!geometry(headless).climbable(up.get(1)),"unknown head cell is not climbable");
        var climber=new RouteMotor("c",up,policy);
        var climbing=climber.tick(new RouteMotor.Pose(3.5,1.2,.5,0,0,0,false,20),g);
        check(climber.active() && climbing.jump() && climbing.forward()==0 && !climbing.sprint(),"jump climbs");
        climber.tick(new RouteMotor.Pose(3.5,2.1,.5,0,0,0,false,20),g);
        check((int)climber.status().get("next_index")==2,"climb arrival advances");
        climber.tick(new RouteMotor.Pose(3.5,3.0,.5,0,0,0,false,20),g);
        check(climber.status().get("state").equals("done"),"top of the climb completes");
        var descender=new RouteMotor("c",List.of(up.get(2),up.get(1)),policy);
        check(!descender.tick(new RouteMotor.Pose(3.5,3.0,.5,0,0,0,false,20),g).jump(),"no jump descends");
        var lost=new RouteMotor("c",up,policy); lost.tick(new RouteMotor.Pose(3.5,1.2,.5,0,0,0,false,20),geometry(floor()));
        check(!lost.active(),"ladder removed mid-route refuses");
        var scaffold=floor();
        for(int y=1;y<=3;y++) scaffold.put(new Key(3,y,0),Map.of("block","minecraft:scaffolding",
                "fluid","minecraft:empty","collision_boxes",List.of(List.of(0,.875,0,1,1,1))));
        var platform=geometry(scaffold);
        var top=new RouteMotor("platform",List.of(new RouteMotor.Point(3,3,0),new RouteMotor.Point(3,4,0)),policy);
        var steer=top.tick(new RouteMotor.Pose(3.8,3.1,.5,0,0,90,false,20),platform);
        check(top.active() && steer.forward()>0 && steer.jump(),"final scaffold climb centres to the land arrival radius");
        top.tick(new RouteMotor.Pose(3.5,4,.5,0,0,0,true,20),platform);
        check(top.status().get("state").equals("done"),"scaffold climb ends standing on its top platform");
        // Real sensor shape (results/scaffolding/20261111T134712226357Z): four posts plus the top rim.
        var real=floor();
        for(int y=1;y<=3;y++) real.put(new Key(3,y,0),Map.of("block","minecraft:scaffolding","fluid","minecraft:empty",
                "collision_boxes",List.of(List.of(0,0,0,.125,1,.125),List.of(0,0,.875,.125,1,1),List.of(.875,0,0,1,1,.125),
                        List.of(.875,0,.875,1,1,1),List.of(0,.875,.125,1,1,.875),List.of(.125,.875,0,.875,1,.125),
                        List.of(.125,.875,.875,.875,1,1)),"properties",Map.of("bottom","false","distance","0")));
        var realTop=new RouteMotor("platform",List.of(new RouteMotor.Point(3,3,0),new RouteMotor.Point(3,4,0)),policy);
        var realSteer=realTop.tick(new RouteMotor.Pose(3.8,3.1,.5,0,0,90,false,20),geometry(real));
        check(realTop.active() && realSteer.forward()>0 && realSteer.jump(),"real seven-box scaffold climb centres");
        realTop.tick(new RouteMotor.Pose(3.5,4,.5,0,0,0,true,20),geometry(real));
        check(realTop.status().get("state").equals("done"),"real seven-box scaffold top is standable");
        var down=new RouteMotor("down",List.of(new RouteMotor.Point(3,3,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(3,1,0)),policy);
        var descent=down.tick(new RouteMotor.Pose(3.5,3,.5,0,0,0,true,20),geometry(real));
        check(down.active() && descent.sneak() && !descent.jump() && !descent.sprint(),"descending inside a scaffolding column holds sneak");
        check(!climber.tick(new RouteMotor.Pose(3.5,3.0,.5,0,0,0,false,20),g).sneak(),"a ladder descent does not sneak");
        var through=new RouteMotor("through",List.of(new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),new RouteMotor.Point(4,1,0)),policy);
        var walk=through.tick(new RouteMotor.Pose(2.5,1,.5,0,0,-90,true,20),geometry(real));
        check(!walk.sneak() && walk.forward()>0,"walking level through a scaffolding cell does not sneak");
        check(geometry(real).standable(new RouteMotor.Point(3,1,0)),"a column cell is enterable from the side despite its sensed posts");
        // Off a bridge into the column's top cell: walk over it on the plate, then sneak to sink in.
        var sink=new RouteMotor("sink",List.of(new RouteMotor.Point(2,4,0),new RouteMotor.Point(3,3,0),new RouteMotor.Point(3,2,0)),policy);
        var walkOver=sink.tick(new RouteMotor.Pose(2.6,4,.5,0,0,-90,true,20),geometry(real));
        check(sink.active() && walkOver.forward()>0 && !walkOver.sneak(),"stepping onto a scaffolding top walks without sneak");
        var sinkIn=sink.tick(new RouteMotor.Pose(3.5,4,.5,0,0,-90,true,20),geometry(real));
        check(sink.active() && sinkIn.sneak() && sinkIn.forward()==0,"over the column top the body sneaks to sink in");
        // Climbing inside scaffolding must not finish below the plate (target.y - 0.5 drops back down).
        var inCol=new RouteMotor("incol",List.of(new RouteMotor.Point(3,1,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(3,3,0)),policy);
        var half=inCol.tick(new RouteMotor.Pose(3.5,2.5,.5,0,0,0,false,20),geometry(real));
        check(inCol.active() && (int)inCol.status().get("next_index")==2 && half.jump(),"scaffolding climb does not stop below target.y");
        inCol.tick(new RouteMotor.Pose(3.5,3.04,.5,0,0,0,false,20),geometry(real));
        check(inCol.status().get("state").equals("done"),"scaffolding climb finishes once feet clear target.y");
    }
    /**
     * RoutePlanner admits any collision-box top as a floor (closed trapdoor 3/16, carpet 1/16, lily pad).
     * House-large run 20261119T133618892664Z: a route over a closed trapdoor at y 69.1875 was refused
     * "route_point" 35 times and the bot stalled in a bedroom with the stairwell open beside it.
     */
    private static void shapeHeightChecks() {
        var cells=floor();
        cells.put(new Key(1,1,0),Map.of("block","minecraft:oak_trapdoor","fluid","minecraft:empty",
                "properties",Map.of("half","bottom","open","false"),"collision_boxes",List.of(List.of(0,0,0,1,.1875,1))));
        var over=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1.1875,0),new RouteMotor.Point(2,1,0));
        var shaped=motor(over);
        check(geometry(cells).standable(over.get(1)),"a closed trapdoor top is a stance");
        var step=shaped.tick(pose(.5,1,.5,-90),geometry(cells));
        check(shaped.active() && step.forward()>0,"a route across a closed trapdoor is followed");
        shaped.tick(pose(1.5,1.1875,.5,-90),geometry(cells));
        check((int)shaped.status().get("next_index")==2,"arrival on the trapdoor top advances");
        reject(()->new RouteMotor("flat",over,new RouteMotor.Policy(true,true,false,3)),"fractional heights still need allow_shapes");
        reject(()->motor(List.of(over.get(0),new RouteMotor.Point(1,Double.POSITIVE_INFINITY,0))),"nonfinite height");
    }
    /**
     * A centre-only request onto scaffolding must honour its arrival radius: the climbable arrival rule
     * accepted .45 and ended every 0.05 centring in one tick (80+ resubmitted routes per drive).
     */
    private static void centerOnlyClimbableChecks() {
        var cells=floor();
        // Standing on a plate inside a scaffolding column: the body cell (3,2,0) is scaffolding too.
        for(int y=1;y<=3;y++) cells.put(new Key(3,y,0),Map.of("block","minecraft:scaffolding","fluid","minecraft:empty",
                "collision_boxes",List.of(List.of(0,.875,0,1,1,1))));
        var policy=new RouteMotor.Policy(true,true,true,3,false,true);
        check(geometry(cells).climbable(new RouteMotor.Point(3,2,0)),"the centring cell is climbable scaffolding");
        var spot=new RouteMotor.Point(3,2,0);
        var centring=new RouteMotor("centre",List.of(spot,spot),policy,true,.05);
        var input=centring.tick(new RouteMotor.Pose(3.8,2,.5,0,0,90,true,20),geometry(cells));
        check(centring.active() && input.forward()>0,"centre-only on scaffolding walks toward the centre instead of finishing");
        centring.tick(new RouteMotor.Pose(3.52,2,.5,0,0,90,true,20),geometry(cells));
        check(centring.status().get("state").equals("done"),"centre-only on scaffolding finishes inside its radius");
    }
    /**
     * A body 0.6 wide climbing a 1-wide vine column must keep its box inside the column: house-large run
     * 20261119T202232979013Z hung 0.22 off centre, its box 0.02 into the next column, head under oak leaves
     * there, and stalled 25 times at the same rise ("route_no_progress", 200 s).
     */
    private static void climbStaysInColumnChecks() {
        var cells=floor();
        var vine=Map.<String,Object>of("block","minecraft:vine","fluid","minecraft:empty","collision_boxes",List.of(),
                "properties",Map.of("west","true"));
        for(int y=1;y<=4;y++) cells.put(new Key(3,y,0),vine);
        var leaves=Map.<String,Object>of("block","minecraft:oak_leaves","fluid","minecraft:empty",
                "collision_boxes",List.of(List.of(0,0,0,1,1,1)));
        cells.put(new Key(2,3,0),leaves); cells.put(new Key(2,4,0),leaves);
        var policy=new RouteMotor.Policy(true,true,true,3,false,true);
        var up=new RouteMotor("vine",List.of(new RouteMotor.Point(3,1,0),new RouteMotor.Point(3,2,0),
                new RouteMotor.Point(3,3,0),new RouteMotor.Point(3,4,0),new RouteMotor.Point(3,5,0)),policy);
        var input=up.tick(new RouteMotor.Pose(3.28,1.2,.5,0,0,-90,false,20),geometry(cells));
        check(up.active() && input.jump() && input.forward()>0,
                "a climber whose box reaches into the next column steers back to the centre: "+input);
        var centred=up.tick(new RouteMotor.Pose(3.5,1.3,.5,0,0,-90,false,20),geometry(cells));
        check(up.active() && centred.jump() && centred.forward()==0,"a centred climber just climbs: "+centred);
    }
    /**
     * Sinking down a scaffolding column the box must stay inside it too: house-large run 20261119T225430670386Z
     * went down 0.202 off centre, its box on the bottom plate of a hanging cell beside the column, and the edge
     * ended route_no_progress (a non-final descent allowed 0.35 off).
     */
    private static void descentStaysInColumnChecks() {
        var cells=floor();
        for(int y=1;y<=4;y++) cells.put(new Key(3,y,0),Map.of("block","minecraft:scaffolding","fluid","minecraft:empty",
                "collision_boxes",List.of(List.of(0,.875,0,1,1,1)),"properties",Map.of("bottom","false","distance","0")));
        var policy=new RouteMotor.Policy(true,true,true,3,false,true);
        var down=new RouteMotor("sink",List.of(new RouteMotor.Point(3,4,0),new RouteMotor.Point(3,3,0),
                new RouteMotor.Point(3,2,0),new RouteMotor.Point(3,1,0)),policy);
        var input=down.tick(new RouteMotor.Pose(3.25,3.6,.5,0,0,-90,false,20),geometry(cells));
        check(down.active() && input.sneak() && input.forward()>0,
                "a body sinking off centre in a scaffolding column steers back to the centre: "+input);
        var centred=down.tick(new RouteMotor.Pose(3.5,3.5,.5,0,0,-90,false,20),geometry(cells));
        check(down.active() && centred.sneak() && centred.forward()==0,"a centred body just sinks: "+centred);
    }
    /** Battery vine-escape (run 20261119T212820324510Z): the edge from the swim stance into the vine column is a
     *  level climb edge, so it held no jump; the body sank 2 blocks under the vine and circled there 10 s. */
    private static void wetClimbEdgeSwimsUpChecks() {
        var cells=box();
        for(int x=0;x<=1;x++) for(int y=1;y<=2;y++) cells.put(new Key(x,y,0),water("0"));
        cells.put(new Key(0,3,0),AIR); cells.put(new Key(0,4,0),AIR); cells.put(new Key(1,6,0),AIR);
        var vine=Map.<String,Object>of("block","minecraft:vine","fluid","minecraft:empty","collision_boxes",List.of(),
                "properties",Map.of("east","true"));
        for(int y=3;y<=5;y++) cells.put(new Key(1,y,0),vine);
        var policy=new RouteMotor.Policy(true,true,true,3,true,true);
        var m=new RouteMotor("vine-escape",List.of(new RouteMotor.Point(0,3,0),new RouteMotor.Point(1,3,0),
                new RouteMotor.Point(1,4,0),new RouteMotor.Point(1,5,0)),policy);
        var g=geometry(cells);
        var afloat=m.tick(new RouteMotor.Pose(.6,2.9,.5,0,0,-90,false,20,true),g);
        check(m.active() && afloat.jump(),"afloat toward a vine at the surface, the swimmer keeps its height: "+afloat);
        var sunk=m.tick(new RouteMotor.Pose(1.5,1.0,.5,0,0,-90,true,20,true),g);
        check(m.active() && sunk.jump(),"sunk under the vine it heads for, the body swims up: "+sunk);
    }
    private static final double DIAGONAL_YAW=-45;
    private static void hole(Map<Key,Map<String,Object>> cells,int x,int z) {
        for(int y=-3;y<=0;y++) cells.put(new Key(x,y,z),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty"));
    }
    private static void wall(Map<Key,Map<String,Object>> cells,int x,int z) {
        cells.put(new Key(x,1,z),stone()); cells.put(new Key(x,2,z),stone());
    }
    /** Kinematic stand-in for the game: apply the yaw delta, then walk or sprint along the heading. */
    private static RouteMotor.Pose drive(RouteMotor.Pose p,RouteMotor.Input in) {
        double yaw=p.yaw()+in.yaw();
        double pace=(in.sprint()?RouteMotor.SPRINT_BLOCKS_PER_TICK:RouteMotor.WALK_BLOCKS_PER_TICK)*in.forward();
        double x=p.x()-Math.sin(Math.toRadians(yaw))*pace, z=p.z()+Math.cos(Math.toRadians(yaw))*pace;
        return new RouteMotor.Pose(x,p.y(),z,0,0,yaw,true,20);
    }
    private static boolean arrives(RouteMotor m,RouteGeometry g,RouteMotor.Pose start) {
        var p=start; var trace=new StringBuilder();
        for(int i=0;i<120&&m.active();i++) {
            var in=m.tick(p,g);
            p=drive(p,in);
            trace.append(String.format("%d x=%.3f z=%.3f yaw=%.1f f=%.2f; ",i,p.x(),p.z(),p.yaw(),in.forward()));
        }
        if(!m.status().get("state").equals("done")) throw new AssertionError(m.status()+" "+trace.substring(Math.max(0,trace.length()-900)));
        return m.status().get("state").equals("done");
    }
    private static void diagonalChecks() {
        var diagonal=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1));
        // Baritone MovementDiagonal cuts over a hole of any depth: both sides open, 4+ deep.
        var holes=floor(); hole(holes,1,0); hole(holes,0,1);
        var g=geometry(holes);
        var start=new RouteMotor.Pose(.5,1,.5,0,0,DIAGONAL_YAW,true,20);
        check(!g.safe(start,diagonal.get(1),1,1.0,false,3),"orthogonal rule: four corners over support, so a hole cut fails");
        check(g.safeDiagonal(start,diagonal.get(1),1,1.0,false,3),"diagonal rule: source and target corners carry the body");
        var cut=motor(diagonal);
        var move=cut.tick(new RouteMotor.Pose(.9,1,.9,0,0,DIAGONAL_YAW,true,20),g);
        check(cut.active() && move.forward()>0 && !move.sprint(),"diagonal over a 5-deep hole is accepted at walk pace");
        check(arrives(motor(diagonal),g,start),"drives across the corner cut and arrives");
        // Two diagonals in a row over four holes: walk, never sprint.
        var chain=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1),new RouteMotor.Point(2,1,2));
        var twin=floor(); hole(twin,1,0); hole(twin,0,1); hole(twin,2,1); hole(twin,1,2);
        var first=motor(chain).tick(start,geometry(twin));
        check(first.forward()>0 && !first.sprint(),"two diagonal holes accepted, walk only");
        check(arrives(motor(chain),geometry(twin),start),"two diagonal holes arrive");
        check(motor(chain).tick(start,geometry(floor())).sprint(),"solid sides keep the sprint through a diagonal run");
        // Edging past one stone side: bend round the blocked column.
        for(var blocked:List.of(new int[]{1,0},new int[]{0,1})) {
            var edge=floor(); wall(edge,blocked[0],blocked[1]);
            var bent=motor(diagonal);
            var turn=bent.tick(start,geometry(edge));
            check(bent.active() && turn.forward()>0 && turn.yaw()!=0 && !turn.sprint(),"edging steers onto the bend waypoint and walks");
            check(arrives(motor(diagonal),geometry(edge),start),"edging follows the bend and arrives");
        }
        // Cohort 25 R3 rep 2: mid-bend, the body 6 degrees off toward the blocked column. The
        // .01 bend clearance must be swept on the exact bearing, not the leftover yaw.
        var tight=floor(); wall(tight,1,0);
        var mid=motor(diagonal); mid.tick(start,geometry(tight));
        double bearing=Math.toDegrees(Math.atan2(-(.69-.5),1.31-.5));
        var skew=mid.tick(new RouteMotor.Pose(.5,1,.5,0,0,bearing-6,true,20),geometry(tight));
        check(mid.active() && skew.forward()>0 && Math.abs(skew.yaw()-6)<1e-6,"bend corrects a small heading error instead of stopping");
        var both=floor(); wall(both,1,0); wall(both,0,1);
        var shut=motor(diagonal); shut.tick(start,geometry(both));
        check(!shut.active() && shut.status().get("reason").equals("route_swept_geometry_unknown_or_unsafe"),"both sides blocked is rejected");
        // Bend pinned against flowing water/obstacle must give up with route_no_progress instead of 200-tick step deadline.
        var pinnedEdge=floor(); wall(pinnedEdge,1,0);
        var pinnedBend=motor(diagonal);
        for(int t=0;t<40 && pinnedBend.active();t++) pinnedBend.tick(new RouteMotor.Pose(.5,1,.5,0,0,DIAGONAL_YAW,true,20,true),geometry(pinnedEdge));
        check(!pinnedBend.active() && pinnedBend.status().get("reason").equals("route_no_progress"),"stalled bend stops with route_no_progress");
        // Recentre pinned off-centre must also give up with route_no_progress instead of 200-tick step deadline.
        var pinnedSide=floor(); pinnedSide.put(new Key(1,1,-1),stone());
        var pinnedCentring=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0)));
        double clipYaw=Math.toDegrees(Math.atan2(-(1.5-.6),.5-.05));
        for(int t=0;t<40 && pinnedCentring.active();t++) pinnedCentring.tick(pose(.6,1,.05,clipYaw),geometry(pinnedSide));
        check(!pinnedCentring.active() && pinnedCentring.status().get("reason").equals("route_no_progress"),"stalled recentre stops with route_no_progress");
        // Cohort 21 R4: a hop overshot its landing and the next sweep clipped a wall while
        // airborne. In the air, coast; once landed the recentre-and-retry takes over.
        var clipped=floor(); wall(clipped,2,1);
        var hop=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0));
        var air=motor(hop); var coast=air.tick(new RouteMotor.Pose(1.6,1.3,.75,0,0,-90,false,20),geometry(clipped));
        check(air.active() && coast.forward()==0 && !coast.jump(),"airborne blocked sweep coasts instead of stopping");
        air.tick(new RouteMotor.Pose(1.6,1,.75,0,0,-90,true,20),geometry(clipped));
        check(air.active(),"landed off-centre: recentres and retries");
        // Lava under a side rejects the cut.
        var lava=floor(); lava.put(new Key(1,0,0),Map.of("block","minecraft:lava","fluid","minecraft:lava","properties",Map.of("level","0"),"collision_boxes",List.of()));
        hole(lava,0,1);
        var burn=motor(diagonal); burn.tick(start,geometry(lava));
        check(!burn.active(),"lava under a side is rejected");
        // Orthogonal edges keep the four-corner rule: a strip of hole beside the target column.
        var strip=floor(); hole(strip,1,-1);
        var beside=new RouteMotor.Pose(.5,1,.05,0,0,-90,true,20);
        check(!geometry(strip).safe(beside,new RouteMotor.Point(1,1,0),1,1.0,false,3),"orthogonal sweep still needs all four corners");
        check(geometry(strip).safeDiagonal(beside,new RouteMotor.Point(1,1,0),1,1.0,false,3),"same sweep passes only under the diagonal rule");
        // Deep hole on a side: no sprint across the cut (cornerSprintOk), shallow is fine.
        check(!g.cornerSprintOk(diagonal.get(0),diagonal.get(1),3) && geometry(floor()).cornerSprintOk(diagonal.get(0),diagonal.get(1),3),"corner sprint only over supported sides");
    }
    private static void staircase(Map<Key,Map<String,Object>> cells,int x,int top) {
        for(int y=1;y<=top;y++) cells.put(new Key(x,y,0),stone());
    }
    private static void nativeSpeedChecks() {
        // 90 degree corner taken at speed: every tick moves, the heading flips in one tick.
        var g=geometry(floor());
        var corner=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),
                new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),new RouteMotor.Point(3,1,1),new RouteMotor.Point(3,1,2),new RouteMotor.Point(3,1,3)));
        var p=new RouteMotor.Pose(.5,1,.5,0,0,-90,true,20); double widest=0; boolean stopped=false;
        for(int i=0;i<120&&corner.active();i++) {
            var in=corner.tick(p,g);
            if(corner.active() && in.forward()==0) stopped=true;
            widest=Math.max(widest,Math.abs(in.yaw()));
            p=drive(p,in);
        }
        check(corner.status().get("state").equals("done"),"corner route completes");
        check(!stopped && widest>=45,"a 90 degree corner (>45 in one tick) is taken without stopping");
        // Sprint stays on through straight and diagonal points.
        var diag=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1),new RouteMotor.Point(2,1,2),new RouteMotor.Point(3,1,3)));
        check(diag.tick(new RouteMotor.Pose(1.3,1,1.3,.2,0,-45,true,20),g).sprint(),"sprint through a diagonal point");
        var line=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),new RouteMotor.Point(4,1,0)));
        check(line.tick(new RouteMotor.Pose(2.9,1,.5,.25,0,-90,true,20),g).sprint(),"sprint through a straight point");
        // Staircase: consecutive ascents keep the sprint jump (Baritone sprintableAscend).
        var stairs=floor(); staircase(stairs,3,1); staircase(stairs,4,2); staircase(stairs,5,3); staircase(stairs,6,3);
        var climb=motor(List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,2,0),
                new RouteMotor.Point(4,3,0),new RouteMotor.Point(5,4,0),new RouteMotor.Point(6,4,0)));
        climb.tick(new RouteMotor.Pose(2.3,1,.5,.25,0,-90,true,20),geometry(stairs));
        var second=climb.tick(new RouteMotor.Pose(3.4,2,.5,.25,0,-90,true,20),geometry(stairs));
        check(second.jump() && second.sprint() && second.forward()==1,"second stair of a staircase sprint-jumps");
        // Descend then ascend in the same direction.
        var dip=floor(); dip.put(new Key(0,1,0),stone()); dip.put(new Key(2,1,0),stone()); dip.put(new Key(3,1,0),stone());
        var valley=motor(List.of(new RouteMotor.Point(0,2,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,2,0),new RouteMotor.Point(3,2,0)));
        var out=valley.tick(new RouteMotor.Pose(1.4,1,.5,.25,0,-90,true,20),geometry(dip));
        check(out.jump() && out.sprint(),"descend into an ascent in the same direction keeps the sprint jump");
        // The frozen profile keeps its 45 degree bound for Python-sent controls.
        BodyRuntime.Controls legacy=new BodyRuntime.Controls("a",0,1,2,1,1,0,90,0,false,false,false,false,false,false,false,0);
        try { BodyRuntime.validateControls(legacy); throw new AssertionError("legacy bound"); }
        catch(IllegalArgumentException expected) { checks++; }
        BodyRuntime.validateControls(new BodyRuntime.Controls("a",0,1,2,1,1,0,45,0,false,false,false,false,false,false,false,0)); checks++;
        // Water landing: a corner hanging over open air beside the landing column.
        var shore=pool();
        for(int x=1;x<=2;x++) { shore.put(new Key(x,0,-1),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty")); shore.put(new Key(x,-1,-1),stone()); }
        var drop=List.of(new RouteMotor.Point(1,5,0),new RouteMotor.Point(2,1,0));
        var edge=new RouteMotor.Pose(1.5,5,.2,0,0,-90,true,20);
        var fall=new RouteMotor("d",drop,SWIM); var walked=fall.tick(edge,geometry(shore));
        check(fall.active() && walked.forward()>0,"water-landing sweep accepts a corner over open air with a known floor");
        var bare=pool(); bare.putAll(shore); bare.remove(new Key(1,-1,-1)); bare.remove(new Key(2,-1,-1));
        var bad=new RouteMotor("d",drop,SWIM); bad.tick(edge,geometry(bare));
        check(!bad.active(),"unknown floor under the open column is refused");
        var lavaBed=pool(); lavaBed.putAll(shore);
        for(int x=1;x<=2;x++) lavaBed.put(new Key(x,-1,-1),Map.of("block","minecraft:lava","fluid","minecraft:lava","properties",Map.of("level","0"),"collision_boxes",List.of()));
        var burn=new RouteMotor("d",drop,SWIM); burn.tick(edge,geometry(lavaBed));
        check(!burn.active(),"lava under the open column is refused");
    }
    private static void resyncAndSwimChecks() {
        var g=geometry(pool());
        // Stale start: walked into a later cell during planning.
        var diagonal=new RouteMotor("r",List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1),new RouteMotor.Point(2,1,1)),new RouteMotor.Policy(true,true,true,3));
        diagonal.tick(pose(1.9,1,1.9,-90),geometry(floor()));
        check(diagonal.active() && (int)diagonal.status().get("next_index")>=1,"grounded in a later cell re-syncs instead of route_start_displaced");
        // Dropped into water beside route[1]/route[2] while the plan was computed.
        var route=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),new RouteMotor.Point(4,1,0));
        var swimmer=new RouteMotor("w",route,SWIM);
        swimmer.tick(wet(3.5,.3,.5),g);
        check(swimmer.active() && (int)swimmer.status().get("next_index")>=2,"swimming in a later column re-syncs");
        // Airborne but within 0.6 of a later point's centre at its height.
        var near=new RouteMotor("n",List.of(new RouteMotor.Point(-2,1,0),new RouteMotor.Point(-1,1,0),new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0)),new RouteMotor.Policy(true,true,true,3));
        near.tick(new RouteMotor.Pose(1.05,1,.5,0,0,-90,false,20),geometry(floor()));
        check(near.active() && (int)near.status().get("next_index")>=2,"within 0.6 of a later centre re-syncs");
        var lost=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0)));
        lost.tick(pose(6.5,1,.5,-90),geometry(floor()));
        check(!lost.active() && lost.status().get("reason").equals("route_start_displaced"),"far from every early point is still displaced");
        // Swim: in the target column but below the stance, rise without steering.
        var up=new RouteMotor("u",route.subList(0,3),SWIM);
        up.tick(pose(1.5,1,.5,-90),g);
        up.tick(wet(2.5,.3,.5),g);
        var rise=up.tick(wet(3.5,-.2,.5),g);
        check(up.active() && rise.forward()==0 && rise.yaw()==0 && rise.jump(),"below the stance in the column: hold jump, no steering");
        var stillRising = up.tick(wet(3.5,.05,.5),g);
        check(up.active() && stillRising.jump(),"final swim point does not stop a block below the requested height");
        up.tick(wet(3.5,.8,.5),g);
        check(up.status().get("state").equals("done"),"final swim point arrives within controller height tolerance");
        // Wading r6 20261112T042556175575Z: the route ended on the surface, all input
        // stopped and the body sank to the bed under its goal before the arrival was read.
        check(up.treading(wet(3.5,.4,.5),g),"a route that ended on a water surface holds the body afloat");
        check(!up.treading(wet(2.5,.4,.5),g),"only in the end column");
        check(!up.treading(new RouteMotor.Pose(3.5,1,.5,0,0,-90,true,20,false),g),"not standing dry");
        check(!new RouteMotor("f",route.subList(0,3),SWIM).treading(wet(3.5,.4,.5),g),"not while still following");
        up.release();
        check(!up.treading(wet(3.5,.4,.5),g),"a cancel ends it");
        check(RouteMotor.afloat(up,wet(2.5,-.5,.5),true,g),"head under water after a cancel: swim up");
        check(RouteMotor.afloat(null,wet(2.5,-.5,.5),true,g),"head under water with no route ever: swim up");
        // companion/20261116T134201634342Z: a dig under water held the body down until it drowned.
        check(RouteMotor.drowning(true,99,300),"head under water, air under a third: drowning");
        check(!RouteMotor.drowning(true,100,300),"air still at a third: keep working");
        check(!RouteMotor.drowning(false,0,300),"head out of water: not drowning");
        var two=pool();
        for(int x=2;x<=4;x++) { two.put(new Key(x,-1,0),water("0")); two.put(new Key(x,-2,0),stone()); }
        check(RouteMotor.afloat(null,wet(2.5,.4,.5),false,geometry(two)),"afloat in water too deep to stand in, head out: hold (float, don't bob)");
        check(RouteMotor.afloat(null,wet(2.5,-.6,.5),false,geometry(two)),"same, feet in the lower cell: hold");
        // wading/20261112T132321148739Z: spawned in one-deep water the body bobbed and the natural run never saw it grounded.
        check(!RouteMotor.afloat(null,wet(2.5,.4,.5),false,g),"one-deep water, head out, not yet grounded: settle on the bed");
        check(!RouteMotor.afloat(null,new RouteMotor.Pose(2.5,0,.5,0,0,-90,true,20,true),false,g),"standing on a shallow bed, head out: no input");
        check(!RouteMotor.afloat(null,new RouteMotor.Pose(3.5,1,.5,0,0,-90,true,20,false),true,g),"not in water: no input");
        // companion/20261112T132940952617Z: leaves one block over the water beside a diagonal pinned the
        // floating body's head; every first edge was then refused and it stood 92 s.
        var low=floor();
        for(int x=2;x<=4;x++) for(int z=0;z<=1;z++) { low.put(new Key(x,0,z),water("0")); low.put(new Key(x,-1,z),stone()); }
        low.put(new Key(3,2,0),stone());
        var under=new RouteMotor.Pose(2.75,.2,.77,0,0,-45,false,20,true);
        check(geometry(low).swimSafe(under,new RouteMotor.Point(3,1,1),1,.3,true),"a level swim passes under a one-block gap over the water");
        low.put(new Key(3,1,0),stone());
        check(!geometry(low).swimSafe(under,new RouteMotor.Point(3,1,1),1,.3,true),"not through a block in the stance cell");
        // On the bed of a 3-deep pool, under a surface-stance start: swim up, not route_start_displaced.
        var deep=pool();
        for(int x=2;x<=4;x++) { deep.put(new Key(x,-1,0),water("0")); deep.put(new Key(x,-2,0),water("0")); deep.put(new Key(x,-3,0),stone()); }
        var sunk=new RouteMotor("s",route.subList(1,4),SWIM);
        var lift=sunk.tick(new RouteMotor.Pose(2.5,-2,.5,0,0,-90,true,20,true),geometry(deep));
        check(sunk.active() && lift.jump(),"sunk under a surface start holds jump instead of route_start_displaced");
        var dry=new RouteMotor("d",route.subList(1,4),SWIM);
        dry.tick(new RouteMotor.Pose(2.5,-2,.5,0,0,-90,true,20,false),geometry(deep));
        check(!dry.active(),"far below the start out of water is still displaced");
        // Vertical +1 rise from 1-deep wading to its water surface is valid under swim policy even without climb,
        // and swimming past an inside wall corner recenters in the water column rather than aborting swept geometry.
        var riseAndTurn=floor();
        riseAndTurn.put(new Key(0,1,0),water("0"));
        riseAndTurn.put(new Key(1,0,0),water("0")); riseAndTurn.put(new Key(1,1,0),water("0"));
        riseAndTurn.put(new Key(2,0,0),water("0")); riseAndTurn.put(new Key(2,1,0),water("0"));
        riseAndTurn.put(new Key(2,0,1),water("0")); riseAndTurn.put(new Key(2,1,1),water("0"));
        riseAndTurn.put(new Key(1,2,1),stone());
        var rg=geometry(riseAndTurn);
        var swimCorner=new RouteMotor("swimCorner",List.of(
                new RouteMotor.Point(0,1,0), new RouteMotor.Point(0,2,0),
                new RouteMotor.Point(1,2,0), new RouteMotor.Point(2,2,0), new RouteMotor.Point(2,2,1)),SWIM);
        var firstRise=swimCorner.tick(wet(.5,1.0,.5),rg);
        check(swimCorner.active() && firstRise.jump(),"vertical surface rise accepted under swim policy");
        swimCorner.tick(wet(1.5,1.7,.5),rg);
        var clipTurn=swimCorner.tick(wet(2.06,1.7,.5),rg);
        check(swimCorner.active() && clipTurn.forward()>0 && clipTurn.jump() && Math.abs(clipTurn.yaw())<1,
                "off-center swim corner recenters instead of aborting route_swept_geometry_unknown_or_unsafe");
        swimCorner.tick(wet(2.45,1.7,.5),rg);
        var finishTurn=swimCorner.tick(wet(2.45,1.7,.5),rg);
        check(swimCorner.active() && finishTurn.forward()>0 && finishTurn.jump(),"resumes swim around corner after recentring");
        lakeDropChecks();
        roofedUnderStanceChecks();
    }
    /** Run 20261116T163035614307Z: drifted under stone below the stance; jumping there goes nowhere. */
    private static void roofedUnderStanceChecks() {
        var cells=box();
        for(int x=1;x<=3;x++) { cells.put(new Key(x,4,0),water("0")); cells.put(new Key(x,5,0),AIR); cells.put(new Key(x,6,0),AIR); }
        for(int y=1;y<=2;y++) cells.put(new Key(2,y,2),water("0"));
        for(int y=1;y<=4;y++) cells.put(new Key(2,y,3),water("0"));
        cells.put(new Key(2,5,3),AIR);
        var g=geometry(cells);
        var m=new RouteMotor("roof",List.of(new RouteMotor.Point(1,5,0),new RouteMotor.Point(2,5,0),new RouteMotor.Point(3,5,0)),SWIM);
        m.tick(wet(1.5,4.3,.5),g);
        var in=m.tick(new RouteMotor.Pose(2.5,.5,2.5,0,0,0,false,20,true),g);
        check(m.active() && in.forward()>0 && in.jump() && Math.abs(in.yaw())<1,"roofed below the stance: swim to the open column (+z), not at the target");
    }
    /** Two-block ledge at z>=-7 (stone y=18..19, stance y=20) overhanging a lake (still water y=-2..0, stance y=1). */
    private static void lakeDropChecks() {
        var cells=new HashMap<Key,Map<String,Object>>();
        for(int x=-2;x<=4;x++) for(int z=-12;z<=-3;z++) for(int y=-3;y<=26;y++) {
            Map<String,Object> c=Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty");
            if(z>=-7 && (y==18 || y==19)) c=stone();
            if(y==-3) c=stone();
            if(y>=-2 && y<=0) c=water("0");
            cells.put(new Key(x,y,z),c);
        }
        var g=geometry(cells);
        var drop=new RouteMotor("lake",List.of(new RouteMotor.Point(1,20,-7),new RouteMotor.Point(1,1,-8),new RouteMotor.Point(1,1,-9)),SWIM);
        drop.tick(new RouteMotor.Pose(1.49,20,-6.75,0,0,2,true,20,false),g);
        check(drop.active(),"known 19-block drop into a lake starts (Baritone MovementFall into water)");
    }
    public static void main(String[] args) {
        var point = new RouteMotor.Point(0, 1, 0);
        var center = new RouteMotor("center", List.of(point, point), new RouteMotor.Policy(false, false, false, 1), true, .1);
        var centerInput = center.tick(pose(.95, 1, .5, 90), geometry(floor()));
        check(center.active() && centerInput.forward() > 0, "off-center pose receives a checked centering command");
        center.tick(pose(.58, 1, .5, 90), geometry(floor()));
        check(center.status().get("state").equals("done"), "center route honors requested precise radius");
        var unknownFloor = floor(); unknownFloor.remove(new Key(0, 0, 0));
        var unsafeCenter = new RouteMotor("unsafe", List.of(point, point), new RouteMotor.Policy(false, false, false, 1), true, .1);
        unsafeCenter.tick(pose(.95, 1, .5, 90), geometry(unknownFloor));
        check(!unsafeCenter.active(), "center route refuses unknown support");
        reject(() -> new RouteMotor("dup", List.of(point, point), new RouteMotor.Policy(false, false, false, 1)),
                "duplicate points require explicit center mode");
        var route=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0));
        var cells=floor(); var motor=motor(route);
        var command=motor.tick(pose(.5,1,.5,-90),geometry(cells));
        check(command.forward()>0 && command.sprint(),"known straight sprint");
        check(command.yaw()==0 && !command.jump(),"straight no turn/jump");
        motor.tick(pose(1.5,1,.5,-90),geometry(cells));
        motor.tick(pose(2.5,1,.5,-90),geometry(cells));
        check(motor.tick(pose(3.5,1,.5,-90),geometry(cells)).forward()==0,"completion releases");
        check(motor.status().get("state").equals("done"),"completed destination");
        motor=motor(route); motor.stop("explicit");
        check(motor.tick(pose(.5,1,.5,-90),geometry(cells)).forward()==0,"cancel cannot move");
        check(motor.status().get("reason").equals("explicit"),"cancel reason retained");
        motor=motor(route); motor.tick(pose(6.5,1,.5,-90),geometry(cells));
        check(!motor.active(),"displaced start rejected");
        var unavailable=motor(route); var missing=floor(); missing.remove(new Key(1,1,0));
        unavailable.tick(pose(.5,1,.5,-90),geometry(missing));
        check(!unavailable.active(),"unknown headroom refuses");
        var changed=motor(route); var hole=floor(); hole.put(new Key(1,0,0),Map.of("block","minecraft:air","collision_boxes",List.of()));
        changed.tick(pose(.5,1,.5,-90),geometry(hole));
        check(!changed.active(),"missing destination support refuses");
        var hazard=floor(); hazard.put(new Key(1,1,0),Map.of("block","minecraft:wither_rose","collision_boxes",List.of()));
        check(!geometry(hazard).standable(route.get(1)),"hazard clearance refuses");
        var wet=floor(); wet.put(new Key(1,1,0),Map.of("block","minecraft:air","fluid","minecraft:water","collision_boxes",List.of()));
        check(geometry(wet).standable(route.get(1)),"one-deep water over a floor is waded");
        var flowingWet=floor(); flowingWet.put(new Key(1,1,0),Map.of("block","minecraft:water","fluid","minecraft:flowing_water","properties",Map.of("level","1"),"collision_boxes",List.of()));
        check(geometry(flowingWet).standable(route.get(1)),"one-deep flowing water over a floor is waded");
        wet.put(new Key(1,2,0),Map.of("block","minecraft:air","fluid","minecraft:water","collision_boxes",List.of()));
        check(!geometry(wet).standable(route.get(1)),"water at head height refuses");
        var blind=floor(); blind.put(new Key(1,1,0),Map.of("block","minecraft:air","fluid","minecraft:water"));
        check(!geometry(blind).standable(route.get(1)),"water without collision data is not waded");
        reject(()->motor(List.of(route.get(0),new RouteMotor.Point(2,1,0))),"ordinary route cannot imply gap");
        reject(()->new RouteMotor("x",List.of(route.get(0),new RouteMotor.Point(1,1,1)),new RouteMotor.Policy(false,false,false,1)),"diagonal permission");
        reject(()->new RouteMotor("x",List.of(route.get(0),new RouteMotor.Point(1,0,0)),new RouteMotor.Policy(false,false,false,0)),"drop permission");
        reject(()->motor(List.of(route.get(0),new RouteMotor.Point(Double.NaN,1,0))),"nonfinite route");
        reject(()->motor(List.of(route.get(0),new RouteMotor.Point(.5,1,0))),"nonintegral column");
        // A drop whose column closed after planning: the body stands on the new block in the
        // landing column and jitters; jitter is not progress (small-house roof stall).
        var closed=floor(); closed.put(new Key(0,3,0),stone()); closed.put(new Key(1,3,0),stone());
        var shut=motor(List.of(new RouteMotor.Point(0,4,0),new RouteMotor.Point(1,1,0)));
        for(int t=0;t<40 && shut.active();t++) shut.tick(pose(t%2==0?1.4:1.6,4,.5,-90),geometry(closed));
        check(!shut.active() && shut.status().get("reason").equals("route_no_progress"),"jitter above a closed drop gives up within 2 s");
        var json=JsonParser.parseString("{\"route_id\":\"r\",\"route\":[[0,1,0],[1,1,0]],\"movement_policy\":{\"allow_diagonals\":true,\"allow_sprint\":true,\"allow_shapes\":true,\"max_drop\":3,\"max_gap\":2,\"allow_swim\":false}}").getAsJsonObject();
        check(RouteMotor.parse(json).active(),"wire roundtrip");
        var bad=json.deepCopy(); bad.getAsJsonObject("movement_policy").addProperty("max_drop",true);
        reject(()->RouteMotor.parse(bad),"coercion rejected");
        var swimming=json.deepCopy(); swimming.getAsJsonObject("movement_policy").addProperty("allow_swim",true);
        check(RouteMotor.parse(swimming).active(),"swim policy admitted");
        var climbing=swimming.deepCopy(); climbing.getAsJsonObject("movement_policy").addProperty("allow_climb",true);
        check(RouteMotor.parse(climbing).active(),"optional climb policy admitted");
        var stray=climbing.deepCopy(); stray.getAsJsonObject("movement_policy").addProperty("allow_fly",true);
        reject(()->RouteMotor.parse(stray),"unknown policy field refused");
        var badClimb=climbing.deepCopy(); badClimb.getAsJsonObject("movement_policy").addProperty("allow_climb",1);
        reject(()->RouteMotor.parse(badClimb),"climb coercion refused");
        var badGap=json.deepCopy(); badGap.getAsJsonObject("movement_policy").addProperty("max_gap",-1);
        reject(()->RouteMotor.parse(badGap),"invalid gap policy");
        var badId=json.deepCopy(); badId.addProperty("route_id",true);
        reject(()->RouteMotor.parse(badId),"route ID type");
        // Known safe near waypoint must not oscillate toward an unsafe farther shortcut.
        var edge=floor();
        for(int z=1;z<=5;z++) edge.put(new Key(1,0,z),Map.of("block","minecraft:air","collision_boxes",List.of()));
        var turning=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(0,1,1),new RouteMotor.Point(0,1,2),new RouteMotor.Point(0,1,3)));
        var turn=turning.tick(pose(.801506,1,.288646,5.363637),geometry(edge));
        // Heading follows the near waypoint (yaw ~8.6), not the shortcut (~5.3); a
        // small safe turn may move in the same tick.
        check(turn.yaw()>8,"near waypoint owns heading");
        var next=turning.tick(pose(.801506,1,.288646,5.363637+turn.yaw()),geometry(edge));
        check(next.forward()>0,"safe near waypoint moves after turn");
        var hungry=new RouteMotor("r",route,new RouteMotor.Policy(true,true,true,3));
        check(!hungry.tick(new RouteMotor.Pose(.5,1,.5,0,0,-90,true,6),geometry(cells)).sprint(),"food gate");
        // Step-down: a ledge at x=0 (stand y=2), then level floor (stand y=1).
        var ledge=floor();
        ledge.put(new Key(0,1,0),Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty"));
        var down=List.of(new RouteMotor.Point(0,2,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0));
        var descent=motor(down);
        // Falling, just short of the landing centre: the old motor eased to ~0.3.
        var falling=new RouteMotor.Pose(1.4,1.6,.5,.2,0,-90,false,20);
        check(descent.tick(falling,geometry(ledge)).forward()==1 && descent.active(),"no braking into a straight-through drop");
        // Momentum carried the body past the landing column onto the next point.
        var landed=descent.tick(pose(2.3,1,.5,-90),geometry(ledge));
        check((int)descent.status().get("next_index")==3,"overshoot onto a later point skips ahead");
        check(landed.forward()>0 && Math.abs(landed.yaw())<=8,"overshoot keeps heading instead of reversing");
        // A drop the route turns at still brakes near its landing centre.
        var corner=motor(List.of(new RouteMotor.Point(0,2,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(1,1,1)));
        var braking=corner.tick(falling,geometry(ledge));
        check(braking.forward()>0 && braking.forward()<.5 && corner.active(),"corner drop brakes");
        // Cliff edge: no floor for x >= 2. The body at x=1.82 overhangs the void.
        var cliff=floor();
        for(int x=2;x<=8;x++) for(int z=-3;z<=8;z++)
            cliff.put(new Key(x,0,z),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty"));
        var overhang=new RouteMotor.Pose(1.82,1,.5,0,0,90,true,20);
        check(geometry(cliff).safe(overhang,new RouteMotor.Point(0,1,0),1,.6,false,3),"may step back from an edge it overhangs");
        var towardVoid=new RouteMotor.Pose(1.82,1,.5,0,0,-90,true,20);
        check(!geometry(cliff).safe(towardVoid,new RouteMotor.Point(1,1,0),1,.6,false,3),"overhang may not grow");
        var onGround=new RouteMotor.Pose(1.5,1,.5,0,0,-90,true,20);
        check(!geometry(cliff).safe(onGround,new RouteMotor.Point(1,1,0),1,.6,false,3),"fully supported body may not start overhanging");
        // Off-centre beside a wall: the direct step would clip (1,1,-1); centre first.
        var walled=floor();
        walled.put(new Key(1,1,-1),Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty"));
        var squeeze=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0)));
        // Facing the target from z=.05: the hitbox's -z side would clip the wall.
        var centre=squeeze.tick(pose(.6,1,.05,Math.toDegrees(Math.atan2(-(1.5-.6),.5-.05))),geometry(walled));
        check(squeeze.active() && centre.forward()>0 && centre.yaw()>8,"clipping step steers back to the centre instead of failing");
        check(squeeze.tick(pose(.5,1,.45,-90),geometry(walled)).forward()==0 && squeeze.active(),"recentred");
        check(squeeze.tick(pose(.5,1,.45,-90),geometry(walled)).forward()>0,"step proceeds from the centre");
        // Corner: steer and keep moving when one tick's turn covers it and the new
        // heading is safe; a turn wider than 45 degrees, or an unsafe one, stops first.
        var bend=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,1)));
        var atBend=new RouteMotor.Pose(1.5,1,.5,.15,0,-90,true,20);
        var steer=bend.tick(atBend,geometry(cells));
        check(bend.active() && steer.forward()>0 && Math.abs(steer.yaw()-45)<1e-9,"corner steers while moving");
        var wide=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(1,1,1)));
        var spin=wide.tick(new RouteMotor.Pose(1.5,1,.5,.15,0,-90+1e-3,true,20),geometry(cells));
        check(wide.active() && spin.forward()>0 && Math.abs(spin.yaw()-(90-1e-3))<1e-6,"90 degree corner steers fully and moves in the same tick");
        // A wall at x=2 beside the turn: the new heading would clip it, so turn in place.
        var walledBend=floor();
        for(int y=1;y<=2;y++) walledBend.put(new Key(2,y,1),Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty"));
        var tight=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(1,1,1)));
        double heading=Math.toDegrees(Math.atan2(-(1.5-1.9),1.5-.5));
        var careful=tight.tick(new RouteMotor.Pose(1.9,1,.5,.15,0,heading-30,true,20),geometry(walledBend));
        check(tight.active() && careful.forward()==0 && Math.abs(careful.yaw()-30)<1e-9,"unsafe heading never moves while turning");
        // Sprint holds into a straight-through step-down and while airborne over it.
        var holdDown=motor(down);
        check(holdDown.tick(new RouteMotor.Pose(.5,2,.5,.2,0,-90,true,20),geometry(ledge)).sprint(),"sprint into a through drop");
        check(holdDown.tick(new RouteMotor.Pose(1.2,1.6,.5,.2,0,-90,false,20),geometry(ledge)).sprint(),"sprint kept while airborne");
        // Near a point the route turns at, sprint still drops.
        var intoBend=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,1)));
        check(intoBend.tick(new RouteMotor.Pose(.9,1,.5,.2,0,-90,true,20),geometry(cells)).sprint(),"sprint carries into a corner when the next heading is safe");
        var walledCorner=floor(); wall(walledCorner,1,1);
        check(!motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,1)))
                .tick(new RouteMotor.Pose(.9,1,.5,.2,0,-90,true,20),geometry(walledCorner)).sprint(),"no sprint when the post-corner sweep clips");
        surfaceChecks(); swimAndLandingChecks(); nativeSpeedChecks(); sprintAscentChecks(); climbChecks(); diagonalChecks(); resyncAndSwimChecks();
        shapeHeightChecks(); centerOnlyClimbableChecks(); climbStaysInColumnChecks(); descentStaysInColumnChecks(); wetClimbEdgeSwimsUpChecks();
        System.out.println("RouteMotor: "+checks+" safety/state checks passed");
    }
}
