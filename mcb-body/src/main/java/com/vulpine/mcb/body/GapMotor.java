package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;
import java.util.Map;

/** Known level one/two-block gaps; independent run-up/jump/landing controller. */
final class GapMotor {
    // Reference: Baritone v1.20.0 MovementParkour: admitted gap, runway, clearance,
    // sprint permission and landing before success. This implementation is original.
    private final String id;
    private final BlockPos source,target;
    private final int width,dx,dz;
    private String state="following",reason="",phase="positioning";
    private int started=-1,launch=-1;
    private boolean airSeen;
    GapMotor(JsonObject request) {
        id=request.get("gap_id").getAsString();
        if(!id.matches("[A-Za-z0-9_.:-]{1,80}")) throw new IllegalArgumentException("gap_id");
        var plan=request.getAsJsonObject("plan");
        var policy=request.getAsJsonObject("movement_policy");
        source=StepMotor.point(plan.getAsJsonArray("source")); target=StepMotor.point(plan.getAsJsonArray("target"));
        int span=Math.abs(source.getX()-target.getX())+Math.abs(source.getZ()-target.getZ());
        if(source.getY()!=target.getY() || (source.getX()!=target.getX() && source.getZ()!=target.getZ()) || span<2 || span>3)
            throw new IllegalArgumentException("gap_shape");
        width=span-1; dx=(target.getX()-source.getX())/span; dz=(target.getZ()-source.getZ())/span;
        var max=policy.get("max_gap"); var sprint=policy.get("allow_sprint");
        if(!max.isJsonPrimitive() || !max.getAsJsonPrimitive().isNumber() || max.getAsDouble()!=max.getAsInt()
                || max.getAsInt()<width || max.getAsInt()>2 || !sprint.isJsonPrimitive()
                || !sprint.getAsJsonPrimitive().isBoolean() || (width==2 && !sprint.getAsBoolean()))
            throw new IllegalArgumentException("gap_permission");
    }
    boolean active() { return state.equals("following"); }
    void stop(String why) { state="needs_replan"; reason=why; }
    Map<String,Object> status() { return Map.of("gap_id",id,"state",state,"reason",reason,"phase",phase,"width",width); }
    private static double wrap(double x) { return ((x+180)%360+360)%360-180; }
    boolean geometry(RouteGeometry.Reader reader) {
        var geometry=new RouteGeometry(reader);
        int sy=source.getY(),span=width+1;
        for(int offset=-1;offset<span+3;offset++) {
            int x=source.getX()+dx*offset,z=source.getZ()+dz*offset;
            if(!geometry.clear(x+.5,z+.5,sy,sy+3.2)) return false;
            if(offset>0 && offset<span) {
                for(int depth=1;depth<=2;depth++) if(!StepMotor.empty(reader.cell(x,sy-depth,z))) return false;
            } else if(!StepMotor.cube(reader.cell(x,sy-1,z)) || !geometry.standable(new RouteMotor.Point(x,sy,z))) return false;
        }
        return true;
    }
    private BodyRuntime.Controls input(int tick,double forward,double turn,boolean jump,boolean sprint) {
        return new BodyRuntime.Controls("native-gap",tick,tick,tick+2,2,forward,0,Math.max(-45,Math.min(45,turn)),0,
                jump,false,sprint,false,false,false,false,0);
    }
    private BodyRuntime.Controls fail(int tick,String why) { stop(why); return input(tick,0,0,false,false); }
    BodyRuntime.Controls tick(ServerPlayer p,int tick,RouteGeometry.Reader reader) {
        if(started<0) started=tick;
        if(tick-started>120) return fail(tick,"native_gap_deadline");
        if(!geometry(reader)) return fail(tick,"native_gap_geometry_changed_or_unknown");
        if(width==2 && p.getFoodData().getFoodLevel()<=6) return fail(tick,"native_gap_food");
        double progress=(p.getX()-source.getX()-.5)*dx+(p.getZ()-source.getZ()-.5)*dz;
        double cross=(p.getX()-source.getX()-.5)*dz-(p.getZ()-source.getZ()-.5)*dx;
        int sy=source.getY(),span=width+1;
        if(Math.abs(cross)>.22 || p.getY()<sy-.1 || p.getY()>sy+1.4 || progress < -1.5 || progress>span+1.5)
            return fail(tick,"native_gap_displaced_or_fell");
        if(!new RouteGeometry(reader).clear(p.getX(),p.getZ(),p.getY(),p.getY()+1.8))
            return fail(tick,"native_gap_body_clearance");
        if(phase.equals("positioning")) {
            if(!p.onGround() || Math.abs(p.getY()-sy)>.1) return fail(tick,"native_gap_runway_ground");
            double x=source.getX()+.5-dx-p.getX(),z=source.getZ()+.5-dz-p.getZ();
            double distance=Math.hypot(x,z);
            if(distance>.08) {
                double turn=wrap(Math.toDegrees(Math.atan2(-x,z))-p.getYRot());
                return input(tick,Math.abs(turn)<8?Math.min(.3,Math.max(.08,distance/2)):0,turn,false,false);
            }
            if(Math.hypot(p.getDeltaMovement().x,p.getDeltaMovement().z)>.03) return input(tick,0,0,false,false);
            phase="runup";
        }
        double turn=wrap(Math.toDegrees(Math.atan2(-dx,dz))-p.getYRot());
        if(phase.equals("runup")) {
            if(Math.abs(turn)>4) return input(tick,0,turn,false,false);
            if(!p.onGround()) return fail(tick,"native_gap_left_runway_before_jump");
            double speed=p.getDeltaMovement().x*dx+p.getDeltaMovement().z*dz;
            if(progress>.72) return fail(tick,"native_gap_takeoff_window");
            boolean jump=progress>=.05 && speed>.1;
            if(jump) { phase="flight"; launch=tick; }
            return input(tick,1,turn,jump,width==2);
        }
        if(!p.onGround()) airSeen=true;
        else if(airSeen && progress>=span-.45 && Math.abs(p.getY()-sy)<.1) {
            state="done"; return input(tick,0,0,false,false);
        } else if(!airSeen && tick-launch>12) return fail(tick,"native_gap_did_not_launch");
        return input(tick,progress>span?.0:1,turn,false,width==2);
    }
}
