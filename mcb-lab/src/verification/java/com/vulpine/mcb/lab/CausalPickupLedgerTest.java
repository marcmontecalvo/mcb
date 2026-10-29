package com.vulpine.mcb.lab;

/** Negative cases prevent inventory-count or nearby-item substitution for causal scoring. */
public final class CausalPickupLedgerTest {
    private static void reject(Runnable action) {
        try { action.run(); throw new AssertionError("expected provenance rejection"); }
        catch(IllegalStateException expected) {}
    }
    private static CausalPickupLedger broken() {
        var ledger=new CausalPickupLedger("body");ledger.begin();
        ledger.created("causal",true,1,true);ledger.complete(true);return ledger;
    }
    public static void main(String[] args) {
        var valid=broken();
        if(!valid.pickup("body","causal",1,0) || !valid.collected()) throw new AssertionError("valid pickup");
        reject(()->valid.pickup("body","causal",1,0));
        var planted=broken();reject(()->planted.pickup("body","planted",1,0));
        var other=broken();reject(()->other.pickup("another_body","causal",1,0));
        var before=new CausalPickupLedger("body");before.begin();before.created("causal",true,1,true);
        reject(()->before.pickup("body","causal",1,0));
        reject(()->before.complete(false));
        var missing=new CausalPickupLedger("body");missing.begin();reject(()->missing.complete(true));
        var excess=new CausalPickupLedger("body");excess.begin();reject(()->excess.created("causal",true,2,true));
        var far=new CausalPickupLedger("body");far.begin();reject(()->far.created("causal",true,1,false));
        var duplicated=new CausalPickupLedger("body");duplicated.begin();duplicated.created("causal",true,1,true);
        reject(()->duplicated.created("second",true,1,true));
        var failed=new CausalPickupLedger("body");failed.begin();failed.complete(false);
        if(failed.broken() || failed.collected()) throw new AssertionError("failed break counted");
        var untouched=broken();if(untouched.pickup("body","causal",1,1)) throw new AssertionError("no transfer");
        System.out.println("Causal ledger: valid chain and ten provenance/adversarial cases verified");
    }
}
