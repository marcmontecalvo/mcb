package com.vulpine.mcb.body;

import java.util.*;

/** Matches the Python staircase and pillar fixtures, including resumable slices. */
public final class TerrainPlannerTest {
    private record Key(int x,int y,int z) { }
    private static RoutePlanner.Node n(int x,int y,int z) { return new RoutePlanner.Node(x,y,z); }
    private static Map<String,Object> cell(String block,boolean solid) { return Map.of("block","minecraft:"+block,"collision_boxes",solid?List.of(List.of(0,0,0,1,1,1)):List.of()); }
    private static Map<Key,Map<String,Object>> cliff(int cliff) {
        var cells=new HashMap<Key,Map<String,Object>>();
        for(int x=-2;x<3;x++) for(int y=-1;y<8;y++) for(int z=-1;z<cliff+5;z++) {
            boolean solid=y<0 || Math.abs(x)==1 || (x==0 && z>=cliff && y<4);
            cells.put(new Key(x,y,z),cell(solid?"bedrock":"air",solid));
        } return cells;
    }
    private static TerrainPlanner.Policy policy(int stack,boolean pillar,int reserve,int places,Set<RoutePlanner.Node> protectedCells) {
        return new TerrainPlanner.Policy(false,true,8,places,reserve,List.of("minecraft:cobblestone","minecraft:dirt"),Set.of(),protectedCells,3,4,0,false,stack,pillar,3,false,false,true);
    }
    private static TerrainPlanner.Result run(Map<Key,Map<String,Object>> cells,RoutePlanner.Node goal,TerrainPlanner.Policy p,int count,int slice) {
        var planner=new TerrainPlanner((x,y,z)->cells.get(new Key(x,y,z)),n(0,0,0),goal,0,p,
                List.of(new TerrainPlanner.Stock(3,"minecraft:cobblestone",count)),Set.of(),Set.of(),1,4,24);
        TerrainPlanner.Result r; int calls=0;
        do { r=planner.advance(slice,Long.MAX_VALUE); if(++calls>10000) throw new AssertionError("search did not terminate"); } while(r.status().equals("searching"));
        return r;
    }
    private static void check(boolean condition,String message) { if(!condition) throw new AssertionError(message); }
    private static TerrainPlanner.Result finish(TerrainPlanner p) {
        TerrainPlanner.Result r; int prior=0, calls=0;
        do { r=p.advance(2,Long.MAX_VALUE); check(r.expanded()-prior<=2,"slice expansion limit"); prior=r.expanded();
            if(++calls>10000) throw new AssertionError("search did not terminate"); } while(r.status().equals("searching"));
        return r;
    }
    private static void pythonFixtures() {
        var cells=new HashMap<Key,Map<String,Object>>();
        for(int x=-2;x<3;x++) for(int y=0;y<8;y++) for(int z=-2;z<7;z++) {
            boolean solid=y==0 || (Math.abs(x)==1 && y<7);
            cells.put(new Key(x,y,z),cell(solid?"bedrock":"air",solid));
        }
        cells.put(new Key(0,1,1),cell("stone",true)); cells.put(new Key(0,2,1),cell("stone",true));
        for(int z=3;z<7;z++) for(int y=1;y<=2;y++) cells.put(new Key(0,y,z),cell("bedrock",true));
        var p=new TerrainPlanner.Policy(true,true,8,8,0,List.of("minecraft:cobblestone","minecraft:dirt"),Set.of(),Set.of(),3,4,0,false,1,false,3,false,false,true);
        var inventory=List.of(new TerrainPlanner.Stock(0,"minecraft:iron_pickaxe",1),new TerrainPlanner.Stock(3,"minecraft:cobblestone",1));
        var planner=new TerrainPlanner((x,y,z)->cells.get(new Key(x,y,z)),n(0,1,0),n(0,3,4),0,p,inventory,Set.of(),Set.of(),1,4,24);
        var paused=planner.advance(2,Long.MIN_VALUE); check(paused.status().equals("searching") && paused.expanded()==0,"expired deadline leaves search untouched");
        var result=finish(planner);
        check(result.status().equals("reached") && result.route().equals(List.of(n(0,1,0),n(0,2,1),n(0,2,2),n(0,3,3),n(0,3,4))),"Python mixed cut/place fixture route");
        check(result.steps().get(0).breaks().equals(List.of(n(0,2,1))) && result.steps().get(1).places().equals(List.of(n(0,1,2))),"Python mixed fixture ordered edits");
        check(cells.get(new Key(0,2,1)).get("block").equals("minecraft:stone") && cells.get(new Key(0,1,2)).get("block").equals("minecraft:air"),"mixed branch overlays leave base unchanged");
        var excluded=new TerrainPlanner((x,y,z)->cells.get(new Key(x,y,z)),n(0,1,0),n(0,3,4),0,p,inventory,Set.of(),Set.of(new TerrainPlanner.Edge(n(0,1,0),n(0,2,1))),1,4,24);
        check(!finish(excluded).route().equals(result.route()),"excluded edge is not traversed");
        var bridge=new HashMap<Key,Map<String,Object>>();
        for(int x=-2;x<3;x++) for(int y=-3;y<6;y++) for(int z=-1;z<7;z++) {
            boolean solid=(y<0 && !(z>=1 && z<=3 && y>=-2)) || (Math.abs(x)==1 && y>=0);
            bridge.put(new Key(x,y,z),cell(solid?(y>=0?"bedrock":"stone"):"air",solid));
        }
        var bp=new TerrainPlanner.Policy(false,true,8,8,0,List.of("minecraft:cobblestone","minecraft:dirt"),Set.of(),Set.of(),3,4,0,true,1,false,3,false,false,true);
        var b=new TerrainPlanner((x,y,z)->bridge.get(new Key(x,y,z)),n(0,0,0),n(0,0,4),0,bp,List.of(new TerrainPlanner.Stock(3,"minecraft:cobblestone",3)),Set.of(),Set.of(),1,4,24);
        var br=finish(b);
        check(br.status().equals("reached") && br.route().equals(List.of(n(0,0,0),n(0,0,1),n(0,0,2),n(0,0,3),n(0,0,4))),"Python bridge fixture route");
        check(br.steps().stream().flatMap(s->s.places().stream()).toList().equals(List.of(n(0,-1,1),n(0,-1,2),n(0,-1,3))),"bridge overlay anchors successive placements");
    }
    private static TerrainPlanner.Result cutTest(String neighbour, boolean fallingAllowed, boolean baritone, boolean withTool) {
        var cells=new HashMap<Key,Map<String,Object>>();
        for(int x=-2;x<=3;x++) for(int y=-1;y<=4;y++) for(int z=-2;z<=2;z++)
            cells.put(new Key(x,y,z),cell(y==-1?"bedrock":"air",y==-1));
        cells.put(new Key(1,0,0),cell("dirt",true));
        cells.put(new Key(1,0,-1),cell("bedrock",true)); cells.put(new Key(1,0,1),cell("bedrock",true));
        if(neighbour.equals("unknown")) cells.remove(new Key(2,0,0));
        if(neighbour.equals("water")) cells.put(new Key(2,0,0),Map.of("block","minecraft:water","fluid","minecraft:water","collision_boxes",List.of()));
        if(neighbour.equals("falling")) cells.put(new Key(2,0,0),cell("sand",true));
        var p=new TerrainPlanner.Policy(true,false,8,0,0,List.of(),Set.of(),Set.of(),3,4,0,false,1,false,3,false,baritone,fallingAllowed);
        var inventory=withTool?List.of(new TerrainPlanner.Stock(0,"minecraft:iron_shovel",1)):List.<TerrainPlanner.Stock>of();
        var planner=new TerrainPlanner((x,y,z)->cells.get(new Key(x,y,z)),n(0,0,0),n(1,0,0),0,p,inventory,Set.of(),Set.of(),1,4,24);
        TerrainPlanner.Result result; do { result=planner.advance(1000,Long.MAX_VALUE); } while(result.status().equals("searching")); return result;
    }
    private static String point(RoutePlanner.Node p) { return p.x()+","+(int)p.y()+","+p.z(); }
    private static void parity() {
        for(int count=0;count<=4;count++) for(int reserve=0;reserve<=1;reserve++) for(int mode=0;mode<3;mode++) {
            var map=new HashMap<Key,Map<String,Object>>();
            for(int x=-2;x<3;x++) for(int y=-3;y<6;y++) for(int z=-1;z<7;z++) {
                boolean solid=(y<0 && !(z>=1 && z<=3 && y>=-2)) || (Math.abs(x)==1 && y>=0);
                map.put(new Key(x,y,z),cell(solid?(y>=0?"bedrock":"stone"):"air",solid));
            }
            if(mode==1) map.remove(new Key(0,-2,2));
            var p=new TerrainPlanner.Policy(false,true,8,8,reserve,List.of("minecraft:cobblestone","minecraft:dirt"),Set.of(),mode==2?Set.of(n(0,-1,2)):Set.of(),3,4,0,true,1,false,3,false,false,true);
            var planner=new TerrainPlanner((x,y,z)->map.get(new Key(x,y,z)),n(0,0,0),n(0,0,4),0,p,List.of(new TerrainPlanner.Stock(3,"minecraft:cobblestone",count)),Set.of(),Set.of(),1,4,24);
            var r=finish(planner); var route=String.join(";",r.route().stream().map(TerrainPlannerTest::point).toList());
            var steps=new ArrayList<String>(); for(var s:r.steps()) steps.add(point(s.target())+":"+String.join("/",s.places().stream().map(TerrainPlannerTest::point).toList()));
            System.out.println(count+"|"+reserve+"|"+mode+"|"+r.status()+"|"+r.expanded()+"|"+route+"|"+String.join(";",steps));
        }
    }
    public static void main(String[] args) {
        if(args.length>0 && args[0].equals("parity")) { parity(); return; }
        pythonFixtures();
        var cells=cliff(4); var result=run(cells,n(0,4,4),policy(3,false,0,8,Set.of()),6,2);
        check(result.status().equals("reached") && result.steps().size()==4,"staircase reaches the observed exit");
        check(result.steps().stream().map(TerrainPlanner.Step::places).toList().equals(List.of(
                List.of(n(0,0,1)),List.of(n(0,0,2),n(0,1,2)),List.of(n(0,0,3),n(0,1,3),n(0,2,3)),List.of())),"staircase support order");
        check(cells.get(new Key(0,0,3)).get("block").equals("minecraft:air"),"branch edits do not mutate observed world");
        var limited=run(cliff(4),n(0,4,4),policy(3,false,1,8,Set.of()),6,1000);
        check(!limited.status().equals("reached"),"reserve prevents sixth placement");
        var budget=run(cliff(4),n(0,4,4),policy(3,false,0,5,Set.of()),6,1000);
        check(!budget.status().equals("reached"),"place budget prevents sixth placement");
        var protectedResult=run(cliff(4),n(0,4,4),policy(3,false,0,8,Set.of(n(0,1,2))),6,1000);
        check(!protectedResult.status().equals("reached"),"protected support is never placed");
        var partial=cliff(5); partial.keySet().removeIf(k->k.y()>4);
        check(run(partial,n(0,4,9),policy(3,false,0,8,Set.of()),6,2).status().equals("incomplete"),"unknown upper clearance blocks macro");
        var pillar=run(cliff(1),n(0,4,5),policy(1,true,0,8,Set.of()),3,2);
        check(pillar.status().equals("segment") && pillar.route().equals(List.of(n(0,0,0),n(0,1,0),n(0,2,0),n(0,3,0),n(0,4,1))),"three pillar placements and exit");
        check(pillar.steps().subList(0,3).stream().allMatch(s->s.kind().equals("pillar") && s.exit().equals(n(0,4,1))),"pillar exit survives forecast");
        check(pillar.equals(run(cliff(1),n(0,4,5),policy(1,true,0,8,Set.of()),3,1000)),"slice size preserves deterministic result");
        check(cutTest("air",false,false,true).status().equals("reached"),"known dry neighbours and tool allow a cut");
        check(!cutTest("air",false,false,false).status().equals("reached"),"cut requires a matching hotbar tool");
        check(!cutTest("unknown",false,false,true).status().equals("reached"),"unknown cut neighbour blocks excavation");
        check(!cutTest("water",false,false,true).status().equals("reached"),"wet cut neighbour blocks excavation");
        check(!cutTest("falling",false,true,true).status().equals("reached"),"disabled falling neighbours block excavation");
        check(cutTest("falling",true,true,true).status().equals("reached"),"supported falling neighbour allowed by policy");
        System.out.println("TerrainPlannerTest passed");
    }
}
