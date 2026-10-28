package com.vulpine.mclab;

/** Constant-space, nearest-shell-first enumeration of an inclusive rectangular volume. */
final class AwarenessVolume {
    static boolean nearRefresh(AwarenessConfig config,long x,long y,long z) {
        x=Math.abs(x); y=Math.abs(y); z=Math.abs(z);
        return x<=config.horizontalRadius() && z<=config.horizontalRadius()
                && (config.verticalRadius()==-1 || y<=config.verticalRadius())
                && ((x<=8 && y<=4 && z<=8) || (x<=2 && y<=8 && z<=2));
    }
    record Cell(int x,int y,int z) {}
    private final int radius, lowY, highY, maximumShell;
    private final int size;

    AwarenessVolume(int radius,int lowY,int highY) {
        if(radius<1 || radius>64) throw new IllegalArgumentException("awareness_radius");
        this.radius=radius; this.lowY=lowY; this.highY=highY;
        maximumShell=Math.max(radius,Math.max(Math.abs(lowY),Math.abs(highY)));
        size=Math.toIntExact(count(maximumShell));
    }

    int size() { return size; }

    private long count(int shell) {
        if(shell<0) return 0;
        long width=2L*Math.min(radius,shell)+1;
        long height=Math.max(0L,(long)Math.min(highY,shell)-Math.max(lowY,-shell)+1);
        return width*width*height;
    }

    Cell at(int index) {
        if(index<0 || index>=size) throw new IndexOutOfBoundsException(index);
        int first=0,last=maximumShell;
        while(first<last) {
            int middle=(first+last)>>>1;
            if(count(middle)>index) last=middle; else first=middle+1;
        }
        int shell=first;
        if(shell==0) return new Cell(0,0,0);
        int offset=Math.toIntExact(index-count(shell-1));
        int extent=Math.min(radius,shell),width=extent*2+1;
        int previousExtent=Math.min(radius,shell-1),previousWidth=previousExtent*2+1;
        int low=Math.max(lowY,-shell),high=Math.min(highY,shell);
        int previousLow=Math.max(lowY,1-shell),previousHigh=Math.min(highY,shell-1);
        int previousHeight=Math.max(0,previousHigh-previousLow+1);
        int plane=width*width;
        if(low<previousLow) {
            if(offset<plane) return new Cell(offset%width-extent,low,offset/width-extent);
            offset-=plane;
        }
        if(high>previousHigh) {
            if(offset<plane) return new Cell(offset%width-extent,high,offset/width-extent);
            offset-=plane;
        }
        if(shell<=radius) {
            int face=width*previousHeight;
            for(int z:new int[]{-shell,shell}) {
                if(offset<face) return new Cell(offset%width-extent,previousLow+offset/width,z);
                offset-=face;
            }
            face=previousWidth*previousHeight;
            for(int x:new int[]{-shell,shell}) {
                if(offset<face) return new Cell(x,previousLow+offset/previousWidth,offset%previousWidth-previousExtent);
                offset-=face;
            }
        }
        throw new IllegalStateException("awareness_shell_enumeration");
    }
}
