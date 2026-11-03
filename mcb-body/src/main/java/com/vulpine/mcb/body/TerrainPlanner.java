package com.vulpine.mcb.body;

import java.util.*;

/** Rolling terrain forecast. Geometry is an immutable observation; edits live only in branches. */
final class TerrainPlanner {
    record Policy(boolean allowBreak, boolean allowPlace, int breakBudget, int placeBudget, int reserveBlocks,
                  List<String> materials, Set<String> protectedBlocks, Set<RoutePlanner.Node> protectedPositions,
                  double breakPenalty, double placementPenalty, double toolWearPenalty, boolean allowBridge,
                  int maxSupportStack, boolean allowPillar, int maxPillarHeight, boolean plannerTickCosts,
                  boolean baritoneMoves, boolean allowFallingNeighbours) { }
    record Stock(int slot, String item, int count) { }
    record Edge(RoutePlanner.Node source, RoutePlanner.Node target) { }
    record Step(RoutePlanner.Node source, RoutePlanner.Node target, List<RoutePlanner.Node> breaks,
                List<RoutePlanner.Node> places, String kind, RoutePlanner.Node exit) { }
    record Result(String status, List<RoutePlanner.Node> route, int expanded, String reason, List<Step> steps) { }
    private record Branch(RoutePlanner.Node node, List<Step> steps, Map<RoutePlanner.Node, Map<String,Object>> edits,
                          List<Stock> stock, Set<RoutePlanner.Node> protectedCells, Set<RoutePlanner.Node> visited,
                          int broken, int placed, double cost) { }
    private record Macro(boolean pillar, int dx, int dz, int rise, int distance) { }
    private static final int[][] DIR = {{1,0},{-1,0},{0,1},{0,-1}};
    private static final Map<String,Object> AIR = Map.of("block","minecraft:air", "fluid","minecraft:empty", "collision_boxes",List.of());
    private static final List<List<Integer>> CUBE = List.of(List.of(0,0,0,1,1,1));
    private static final Set<String> FALLING = Set.of("minecraft:sand","minecraft:red_sand","minecraft:gravel","minecraft:suspicious_sand","minecraft:suspicious_gravel");
    private final RouteGeometry.Reader reader;
    private final RoutePlanner.Node start, goal;
    private final double radius;
    private final Policy policy;
    private final Set<Edge> excluded;
    private final int maxDrop, horizon, width;
    private final List<Macro> macros = new ArrayList<>();
    private final List<Branch> macroResults = new ArrayList<>(), goals = new ArrayList<>();
    private final Map<RoutePlanner.Node,Double> walkingCosts = new HashMap<>();
    private List<Branch> layer, next = new ArrayList<>();
    private int macroIndex, expanded, depth, index;
    private boolean macroSearching = true, macroUnknown;
    private Result terminal;

    TerrainPlanner(RouteGeometry.Reader reader, RoutePlanner.Node start, RoutePlanner.Node goal, double radius,
                   Policy policy, List<Stock> inventory, Set<RoutePlanner.Node> protectedPositions,
                   Set<Edge> excluded, int maxDrop, int horizon, int width) {
        if (horizon < 3 || horizon > 6 || width < 1 || width > 64) throw new IllegalArgumentException("Invalid terrain horizon or beam width");
        if (start.y() != Math.rint(start.y()) || goal.y() != Math.rint(goal.y())) throw new IllegalArgumentException("Terrain nodes must be integral");
        this.reader=reader; this.start=start; this.goal=goal; this.radius=radius; this.policy=policy;
        this.excluded=Set.copyOf(excluded); this.maxDrop=maxDrop; this.horizon=horizon; this.width=width;
        layer = List.of(new Branch(start,List.of(),Map.of(),List.copyOf(inventory),Set.copyOf(protectedPositions),Set.of(start),0,0,0));
        walkingCosts.put(start,0.0);
        for (var d : DIR) for (int rise=2; rise<=Math.min(4,policy.maxSupportStack()+1); rise++)
            for (int distance=rise; distance<13; distance++) if (policy.maxSupportStack()>1 && toward(d) && start.y()+rise<=goal.y())
                macros.add(new Macro(false,d[0],d[1],rise,distance));
        for (var d : DIR) for (int rise=2; rise<=policy.maxPillarHeight()+1; rise++)
            for (int distance=1; distance<13; distance++) if (policy.allowPillar() && toward(d) && start.y()+rise<=goal.y())
                macros.add(new Macro(true,d[0],d[1],rise,distance));
    }
    private boolean toward(int[] d) { return (goal.x()-start.x())*d[0]+(goal.z()-start.z())*d[1]>0; }
    private static RoutePlanner.Node n(int x, double y, int z) { return new RoutePlanner.Node(x,y,z); }
    private static RoutePlanner.Node add(RoutePlanner.Node p,int x,int y,int z) { return n(p.x()+x,p.y()+y,p.z()+z); }
    private Map<String,Object> at(Branch b, RoutePlanner.Node p) { return b.edits().containsKey(p) ? b.edits().get(p) : reader.cell(p.x(),(int)p.y(),p.z()); }
    private static boolean dry(Map<String,Object> c) { return c!=null && "minecraft:empty".equals(c.getOrDefault("fluid","minecraft:empty")); }
    private static boolean empty(Map<String,Object> c) { return dry(c) && ("minecraft:air".equals(c.get("block")) || List.of().equals(c.get("collision_boxes"))); }
    private static boolean full(Map<String,Object> c) {
        if (!dry(c) || !(c.get("collision_boxes") instanceof List<?> boxes)) return false;
        for (Object o: boxes) if (o instanceof List<?> box && box.size()==6) {
            boolean match=true; for(int i=0;i<6;i++) if (!(box.get(i) instanceof Number v) || v.doubleValue()!=(i<3?0:1)) match=false;
            if(match) return true;
        } return false;
    }
    private boolean stand(Branch b,RoutePlanner.Node p) { return full(at(b,add(p,0,-1,0))) && empty(at(b,p)) && empty(at(b,add(p,0,1,0))); }
    private boolean knownStep(Branch b, RoutePlanner.Node a, RoutePlanner.Node t, int drop) {
        int dy=(int)(t.y()-a.y());
        if (Math.abs(t.x()-a.x())+Math.abs(t.z()-a.z())!=1 || dy < -drop || dy>1 || !stand(b,a) || !stand(b,t)) return false;
        if (dy>0 && !(empty(at(b,add(a,0,2,0))) && empty(at(b,add(a,0,3,0))) && empty(at(b,add(t,0,2,0))))) return false;
        if (dy<0) for(int y=(int)t.y();y<=(int)a.y()+1;y++) if(!empty(at(b,n(t.x(),y,t.z())))) return false;
        return true;
    }
    private boolean goal(RoutePlanner.Node p) { return p.y()==goal.y() && Math.hypot(p.x()-goal.x(),p.z()-goal.z())<=radius; }
    private double distance(RoutePlanner.Node p) { return Math.abs(p.x()-goal.x())+Math.abs(p.z()-goal.z())+4*Math.abs(p.y()-goal.y()); }
    private final Comparator<Branch> rank = Comparator.comparingDouble((Branch b)->b.cost()+distance(b.node()))
            .thenComparingInt(b->b.broken()+b.placed()).thenComparingDouble(b->distance(b.node()))
            .thenComparingInt(b->b.node().x()).thenComparingDouble(b->b.node().y()).thenComparingInt(b->b.node().z());
    private static String tool(String block, boolean wide) {
        if (block==null) return null;
        String name=block.startsWith("minecraft:")?block.substring(10):"";
        if (Set.of("stone","cobblestone").contains(name)) return "pickaxe";
        if (Set.of("dirt","grass_block").contains(name)) return "shovel";
        if (!wide) return null;
        if (Set.of("terracotta","andesite","diorite","granite","tuff","calcite","deepslate","sandstone","red_sandstone","coal_ore","iron_ore").contains(name)) return "pickaxe";
        for(String color:List.of("white","orange","magenta","light_blue","yellow","lime","pink","gray","light_gray","cyan","purple","blue","brown","green","red","black")) if(name.equals(color+"_terracotta")) return "pickaxe";
        if (Set.of("coarse_dirt","rooted_dirt","podzol","mycelium","clay","mud","snow_block","sand","red_sand","gravel").contains(name)) return "shovel";
        for(String wood: List.of("oak","spruce","birch","jungle","acacia","dark_oak","mangrove","cherry","azalea"))
            if (name.equals(wood+"_leaves") || (!wood.equals("azalea") && name.equals(wood+"_log"))) return "axe";
        return null;
    }
    private boolean hasTool(List<Stock> stock,String kind) {
        for (Stock s:stock) if(s.slot()>=0 && s.slot()<9 && s.count()>0)
            for(String tier:List.of("netherite","diamond","iron","stone","golden","wooden"))
                if(s.item().equals("minecraft:"+tier+"_"+kind)) return true;
        return false;
    }
    private int supply(List<Stock> stock) {
        int total=0; for(Stock s:stock) if(s.slot()>=0 && s.slot()<9 && policy.materials().contains(s.item())) total+=s.count();
        if(total<=policy.reserveBlocks()) return -1;
        for(String material:policy.materials()) for(int i=0;i<stock.size();i++) { Stock s=stock.get(i); if(s.slot()>=0 && s.slot()<9 && s.count()>0 && s.item().equals(material)) return i; }
        return -1;
    }
    private boolean protectedAt(Branch b,RoutePlanner.Node p, Set<RoutePlanner.Node> extra) {
        Map<String,Object> c=at(b,p); return b.protectedCells().contains(p) || policy.protectedPositions().contains(p) || extra.contains(p) || (c!=null && c.get("block") instanceof String block && policy.protectedBlocks().contains(block));
    }
    private boolean cut(Branch b,RoutePlanner.Node p) {
        Map<String,Object> c=at(b,p); String block=c==null?null:(String)c.get("block"), kind=tool(block,policy.plannerTickCosts());
        if(protectedAt(b,p,b.visited()) || !policy.allowBreak() || kind==null || !full(c) || !hasTool(b.stock(),kind)) return false;
        int[][] around={{1,0,0},{-1,0,0},{0,1,0},{0,0,1},{0,0,-1}};
        for(int[] d:around) { var q=add(p,d[0],d[1],d[2]); var adjacent=at(b,q);
            if(!dry(adjacent)) return false;
            if(adjacent.get("block") instanceof String blockName && FALLING.contains(blockName) && (!policy.baritoneMoves() || !policy.allowFallingNeighbours() || (d[1]==0 && !full(at(b,add(q,0,-1,0)))))) return false;
        } return true;
    }
    private Step prepare(Branch b,RoutePlanner.Node t) {
        RoutePlanner.Node s=b.node(); int dy=(int)(t.y()-s.y());
        if(dy<0 || dy>1 || !stand(b,s)) return null;
        List<RoutePlanner.Node> places=new ArrayList<>(), cuts=new ArrayList<>(); var support=add(t,0,-1,0);
        if(!full(at(b,support))) {
            if(!policy.allowPlace()) return null; places.add(support);
            while(true) { var p=places.get(places.size()-1); var below=add(p,0,-1,0);
                boolean anchored=full(at(b,below)) || (policy.allowBridge() && p.y()==s.y()-1 && Math.abs(p.x()-s.x())+Math.abs(p.z()-s.z())==1 && empty(at(b,below)) && full(at(b,add(s,0,-1,0))));
                if(anchored) break; if(places.size()>=policy.maxSupportStack()) return null; places.add(below);
            }
            Collections.reverse(places); if(places.size()>policy.placeBudget()-b.placed()) return null;
            List<Stock> supply=new ArrayList<>(b.stock());
            for(var p:places) { if(protectedAt(b,p,b.visited()) || !empty(at(b,p))) return null; int i=supply(supply); if(i<0) return null; Stock v=supply.get(i); supply.set(i,new Stock(v.slot(),v.item(),v.count()-1)); }
        }
        List<RoutePlanner.Node> clearance=new ArrayList<>(List.of(t,add(t,0,1,0)));
        if(dy>0) clearance.addAll(List.of(add(s,0,2,0),add(s,0,3,0),add(t,0,2,0)));
        for(var p:new LinkedHashSet<>(clearance)) if(!empty(at(b,p))) { if(!cut(b,p)) return null; cuts.add(p); }
        if(cuts.size()>Math.min(4,policy.breakBudget()-b.broken()) || (cuts.isEmpty() && places.isEmpty())) return null;
        return new Step(s,t,List.copyOf(cuts),List.copyOf(places),"step",null);
    }
    private Branch apply(Branch b,Step step) {
        Map<RoutePlanner.Node,Map<String,Object>> edits=new HashMap<>(b.edits());
        for(var p:step.breaks()) edits.put(p,AIR);
        List<Stock> stock=new ArrayList<>(b.stock());
        for(var p:step.places()) { int i=supply(stock); if(i<0) return null; Stock s=stock.get(i);
            if(!Set.of("minecraft:dirt","minecraft:cobblestone").contains(s.item())) return null;
            stock.set(i,new Stock(s.slot(),s.item(),s.count()-1)); edits.put(p,Map.of("block",s.item(),"fluid","minecraft:empty","collision_boxes",CUBE)); }
        List<Step> steps=new ArrayList<>(b.steps()); steps.add(step);
        Set<RoutePlanner.Node> protectedCells=new HashSet<>(b.protectedCells()); protectedCells.addAll(step.places());
        Set<RoutePlanner.Node> visited=new HashSet<>(b.visited()); visited.add(step.target());
        double cost=(step.target().y()!=step.source().y()?1.4:1)+(policy.breakPenalty()+policy.toolWearPenalty())*step.breaks().size()+policy.placementPenalty()*step.places().size();
        return new Branch(step.target(),List.copyOf(steps),edits,List.copyOf(stock),protectedCells,visited,b.broken()+step.breaks().size(),b.placed()+step.places().size(),b.cost()+cost);
    }
    private List<Branch> children(Branch b) {
        List<Branch> out=new ArrayList<>();
        for(var d:DIR) for(int dy:new int[]{0,1,-1}) { var t=add(b.node(),d[0],dy,d[1]);
            if(b.visited().contains(t) || excluded.contains(new Edge(b.node(),t)) || dy < -maxDrop) continue;
            Step step=knownStep(b,b.node(),t,maxDrop)?new Step(b.node(),t,List.of(),List.of(),"step",null):prepare(b,t);
            if(step==null) continue; Branch next=apply(b,step); if(next!=null && knownStep(next,b.node(),t,1)) out.add(next);
        } return out;
    }
    private Branch child(Branch b,RoutePlanner.Node target) { for(Branch c:children(b)) if(c.node().equals(target)) return c; return null; }
    private Branch macro(Macro m) {
        var exit=add(start,m.dx()*m.distance(),m.rise(),m.dz()*m.distance()); Branch b=layer.get(0);
        if(!stand(b,exit)) { var a=at(b,exit); var h=at(b,add(exit,0,1,0));
            if(full(at(b,add(exit,0,-1,0))) && (a==null || h==null) && (m.pillar() || ((a==null || empty(a)) && (h==null || empty(h))))) macroUnknown=true;
            return null; }
        if(!m.pillar()) {
            for(int offset=1;offset<=Math.max(m.distance(),horizon);offset++) {
                var t=add(start,m.dx()*offset,Math.min(m.rise(),Math.max(0,offset-(m.distance()-m.rise()))),m.dz()*offset);
                List<RoutePlanner.Node> clearance=new ArrayList<>(List.of(t,add(t,0,1,0)));
                if(t.y()>b.node().y()) clearance.addAll(List.of(add(b.node(),0,2,0),add(b.node(),0,3,0),add(t,0,2,0)));
                for(var p:clearance) if(at(b,p)==null) { macroUnknown=true; return null; }
                b=child(b,t); if(b==null) return null; if(goal(b.node())) break;
            } return b.placed()>0?b:null;
        }
        for(int offset=1;offset<m.distance();offset++) { b=child(b,add(start,m.dx()*offset,0,m.dz()*offset)); if(b==null) return null; }
        for(int i=0;i<m.rise()-1;i++) {
            var s=b.node(); for(int h=0;h<4;h++) if(at(b,add(s,0,h,0))==null) { macroUnknown=true; return null; }
            if(!policy.allowPillar() || !policy.allowPlace() || b.placed()>=policy.placeBudget() || Math.abs(exit.x()-s.x())+Math.abs(exit.z()-s.z())!=1 || exit.y()-s.y()<1 || exit.y()-s.y()>policy.maxPillarHeight()+1 || !stand(b,exit) || !stand(b,s) || protectedAt(b,s,Set.of()) || !empty(at(b,add(exit,0,2,0))) || supply(b.stock())<0) return null;
            for(int h=0;h<4;h++) if(!empty(at(b,add(s,0,h,0)))) return null;
            var t=add(s,0,1,0); if(excluded.contains(new Edge(s,t))) return null;
            b=apply(b,new Step(s,t,List.of(),List.of(s),"pillar",exit)); if(b==null) return null;
        }
        int offset=m.distance(); while(b.steps().size()<horizon || offset==m.distance()) {
            b=child(b,add(start,m.dx()*offset,m.rise(),m.dz()*offset)); if(b==null) return null; if(goal(b.node())) break; offset++;
        } return b;
    }
    private Result result(String status,Branch b,String reason) {
        List<Step> steps=b==null?List.of():b.steps(); List<RoutePlanner.Node> route=new ArrayList<>(); route.add(start); for(Step s:steps) route.add(s.target());
        return new Result(status,List.copyOf(route),expanded,reason,steps);
    }
    Result advance(int nodeLimit,long deadlineNanos) {
        if(nodeLimit<1) throw new IllegalArgumentException("Invalid terrain search budget"); if(terminal!=null) return terminal;
        int spent=0; while(spent<nodeLimit && System.nanoTime()<deadlineNanos) {
            if(macroSearching) {
                if(macroIndex==macros.size()) { macroSearching=false;
                    if(!macroResults.isEmpty()) { Branch best=Collections.min(macroResults,rank); List<Step> steps=List.copyOf(best.steps().subList(0,Math.min(horizon,best.steps().size())));
                        Branch trimmed=new Branch(best.node(),steps,best.edits(),best.stock(),best.protectedCells(),best.visited(),best.broken(),best.placed(),best.cost());
                        return terminal=result(goal(steps.get(steps.size()-1).target())?"reached":"segment",trimmed,"verified construction macro; four-move forecast"); }
                    if(macroUnknown) return terminal=result("incomplete",null,"construction macro needs known approach/exit geometry"); continue;
                }
                expanded++; spent++; Branch b=macro(macros.get(macroIndex++)); if(b!=null) macroResults.add(b); continue;
            }
            if(index==layer.size()) { depth++;
                if(depth==horizon || next.isEmpty()) {
                    if(!goals.isEmpty()) return terminal=result("reached",Collections.min(goals,Comparator.comparingDouble(Branch::cost)),"goal within terrain horizon");
                    if(depth==horizon && !next.isEmpty()) { List<Branch> candidates=new ArrayList<>();
                        for(Branch b:next) if(distance(b.node())<distance(start) && ((b.broken()==0 && b.placed()==0) || walkingCosts.getOrDefault(b.node(),Double.POSITIVE_INFINITY)>b.cost())) candidates.add(b);
                        return terminal=result(candidates.isEmpty()?"incomplete":"segment",candidates.isEmpty()?null:Collections.min(candidates,rank),candidates.isEmpty()?"no terrain work or walking progress within horizon":"four-move terrain forecast"); }
                    return terminal=result("incomplete",null,"insufficient known terrain horizon");
                }
                next.sort(rank); layer=new ArrayList<>(next.subList(0,Math.min(width,next.size()))); next=new ArrayList<>(); index=0;
            }
            Branch branch=layer.get(index++); expanded++; spent++;
            for(Branch c:children(branch)) { if(c.broken()==0 && c.placed()==0) walkingCosts.merge(c.node(),c.cost(),Math::min); if(goal(c.node())) goals.add(c); else next.add(c); }
        } return result("searching",null,"terrain search slice");
    }
}
