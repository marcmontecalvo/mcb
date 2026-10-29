package com.vulpine.mcb.lab;

/** Pure provenance state machine shared by native hooks and offline contract verification. */
public final class CausalPickupLedger {
    private final String owner;
    private boolean breaking,broken,collected;
    private String drop="";
    public CausalPickupLedger(String owner) { this.owner=owner; }
    public void begin() {
        if (breaking || broken) throw new IllegalStateException("duplicate_target_break");
        breaking=true;
    }
    public boolean created(String uuid,boolean expectedItem,int count,boolean nearTarget) {
        if(!breaking) return false;
        if(!expectedItem || count!=1 || !nearTarget || !drop.isEmpty())
            throw new IllegalStateException("unexpected_target_drop");
        drop=uuid; return true;
    }
    public void complete(boolean success) {
        if(!breaking) return;
        breaking=false;
        if(!success) {
            if(!drop.isEmpty()) throw new IllegalStateException("drop_from_failed_break");
            return;
        }
        if(drop.isEmpty()) throw new IllegalStateException("target_break_without_causal_drop");
        broken=true;
    }
    public boolean pickup(String actor,String uuid,int before,int after) {
        if(after>=before) return false;
        if(!broken || collected || !owner.equals(actor) || !drop.equals(uuid) || before-after!=1)
            throw new IllegalStateException("untracked_item_pickup");
        collected=true;return true;
    }
    public boolean breaking() { return breaking; }
    public boolean broken() { return broken; }
    public boolean collected() { return collected; }
    public String drop() { return drop; }
}
