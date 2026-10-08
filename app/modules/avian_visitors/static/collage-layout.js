/* Shared AvianVisitors silhouette packing; see ../PROVENANCE.md. */
(function(){"use strict";
var FLY_PROB=.15,poseBySpecies=Object.create(null);
  function hash(text){var value=2166136261;for(var i=0;i<text.length;i++)value=Math.imul(value^text.charCodeAt(i),16777619);return value>>>0;}
  /* Original AvianVisitors raster-mask nester, adapted only at the data edge. */
  function maskCells(mask){
    if(!mask||!mask.bits||!mask.w||!mask.h)return null;
    try{var raw=atob(mask.bits),cells=[];for(var i=0;i<mask.w*mask.h;i++)if(raw.charCodeAt(i>>3)&(128>>(i&7)))cells.push([i%mask.w,Math.floor(i/mask.w)]);return cells.length?{w:mask.w,h:mask.h,cells:cells}:null;}catch(_){return null;}
  }
  function chooseAsset(item){
    var perched=item.assets&&item.assets.perched,flight=item.assets&&item.assets.flight,key=item.identity_id||item.scientific_name,pose=poseBySpecies[key];
    if(!pose){pose=perched&&flight?(hash(key)%1000<FLY_PROB*1000?"flight":"perched"):perched?"perched":flight?"flight":null;poseBySpecies[key]=pose;}
    if(pose==="perched"&&!perched)pose=flight?"flight":null;if(pose==="flight"&&!flight)pose=perched?"perched":null;
    return pose?{pose:pose,data:item.assets[pose]}:null;
  }
  function tuning(n){return{packingBudgetFrac:n<=4?.46:n<=12?.40:n<=24?.34:.28,countExp:n<=25?.52:.65,minTileAreaFrac:n<=8?.0100:n<=20?.0075:.0055,ellipseAspectBias:2.1,postScaleMax:n<=4?2.40:n<=12?2.10:n<=25?1.75:1};}
  var GRID_STRIDE=4,COLLAGE_PAD=3;
  function maskPack(tiles,W,H,xBias,yBias,pad){
    var GW=Math.ceil(W/GRID_STRIDE)+2,GH=Math.ceil(H/GRID_STRIDE)+2,grid=new Uint8Array(GW*GH);
    function range(tile,tx,ty,c){var sx=tile.fullW/tile.mask.w,sy=tile.fullH/tile.mask.h,x0=(tx+c[0]*sx)/GRID_STRIDE|0,y0=(ty+c[1]*sy)/GRID_STRIDE|0,x1=(tx+(c[0]+1)*sx)/GRID_STRIDE|0,y1=(ty+(c[1]+1)*sy)/GRID_STRIDE|0;return[Math.max(0,x0),Math.max(0,y0),Math.min(GW-1,x1),Math.min(GH-1,y1)];}
    function collides(tile,tx,ty){for(var i=0;i<tile.mask.cells.length;i++){var r=range(tile,tx,ty,tile.mask.cells[i]);for(var y=r[1];y<=r[3];y++)for(var x=r[0];x<=r[2];x++)if(grid[y*GW+x])return true;}return false;}
    function stamp(tile,tx,ty){for(var i=0;i<tile.mask.cells.length;i++){var r=range(tile,tx,ty,tile.mask.cells[i]),y0=Math.max(0,r[1]-pad),y1=Math.min(GH-1,r[3]+pad),x0=Math.max(0,r[0]-pad),x1=Math.min(GW-1,r[2]+pad);for(var y=y0;y<=y1;y++)for(var x=x0;x<=x1;x++)grid[y*GW+x]=1;}}
    function off(tile,x,y){return x<0||y<0||x+tile.fullW>W||y+tile.fullH>H;}
    var cx=W/2,cy=H/2,placed=[],seed=0x9E3779B9;tiles.sort(function(a,b){return b.fullW*b.fullH-a.fullW*a.fullH;});function rand(){seed=seed*16807%2147483647;return seed/2147483647;}
    tiles.forEach(function(tile,index){if(!index){tile.x=cx-tile.fullW/2;tile.y=cy-tile.fullH/2;stamp(tile,tile.x,tile.y);placed.push(tile);return;}var comX=0,comY=0,comW=0;placed.forEach(function(p){var a=p.fullW*p.fullH;comX+=(p.x+p.fullW/2)*a;comY+=(p.y+p.fullH/2)*a;comW+=a;});comX/=comW;comY/=comW;var best=null,bestCost=Infinity,step=Math.max(GRID_STRIDE,Math.min(tile.fullW,tile.fullH)*.05),maxR=Math.max(W,H),foundRing=-1,phase=rand()*Math.PI*2;for(var radius=0;radius<=maxR;radius+=step){if(foundRing>=0&&radius>foundRing+step*2)break;var samples=Math.max(36,Math.floor(radius/1.6));for(var k=0;k<samples;k++){var theta=phase+k/samples*Math.PI*2,x=cx+radius*xBias*Math.cos(theta)-tile.fullW/2,y=cy+radius*yBias*Math.sin(theta)-tile.fullH/2;if(off(tile,x,y)||collides(tile,x,y))continue;var dx=x+tile.fullW/2-comX,dy=y+tile.fullH/2-comY,cost=Math.hypot(dx/xBias,dy/yBias)+rand()*step*.5;if(cost<bestCost){bestCost=cost;best={x:x,y:y};}}if(best&&foundRing<0)foundRing=radius;}if(best){tile.x=best.x;tile.y=best.y;stamp(tile,best.x,best.y);}else{tile.x=-99999;tile.y=-99999;}placed.push(tile);});return placed;
  }
  function bounds(all){var L=Infinity,R=-Infinity,Top=Infinity,B=-Infinity;all.forEach(function(t){if(t.x<-1000)return;L=Math.min(L,t.x);R=Math.max(R,t.x+t.fullW);Top=Math.min(Top,t.y);B=Math.max(B,t.y+t.fullH);});return{L:L,R:R,T:Top,B:B};}
  function expandPacked(tiles,W,H,maxScale,narrow,exportMode){
    var b=bounds(tiles);if(!isFinite(b.L)||maxScale<=1)return b;
    var marginX=exportMode?16:Math.max(narrow?10:18,W*(narrow?.035:.045)),marginY=exportMode?16:Math.max(narrow?10:18,H*(narrow?.04:.055));
    var scale=Math.min(maxScale,(W-marginX*2)/(b.R-b.L),(H-marginY*2)/(b.B-b.T));if(scale<=1.02)return b;
    var left=(W-(b.R-b.L)*scale)/2,top=(H-(b.B-b.T)*scale)/2;
    tiles.forEach(function(t){if(t.x<-1000)return;t.x=left+(t.x-b.L)*scale;t.y=top+(t.y-b.T)*scale;t.fullW*=scale;t.fullH*=scale;});
    return bounds(tiles);
  }
  function edgePath(mask){
    if(!mask)return"M 8 18 Q 50 2 92 18";var tops=new Array(mask.w),i,x;
    for(i=0;i<mask.cells.length;i++){x=mask.cells[i][0];var y=mask.cells[i][1];if(tops[x]===undefined||y<tops[x])tops[x]=y;}
    var points=[];for(x=0;x<mask.w;x+=Math.max(1,Math.floor(mask.w/14)))if(tops[x]!==undefined)points.push([(x/mask.w)*100,Math.max(4,(tops[x]/mask.h)*100-3)]);
    return points.length<2?"M 8 18 Q 50 2 92 18":"M "+points.map(function(p){return p[0].toFixed(1)+" "+p[1].toFixed(1);}).join(" L ");
  }

function arrange(items,W,H,options){var exportMode=!!(options&&options.exportMode),maxUpscale=options&&options.maxUpscale||1.5;
    var T=tuning(items.length);if(exportMode){T.packingBudgetFrac=.72;T.ellipseAspectBias=W/H;T.postScaleMax=4;}var budget=W*H*T.packingBudgetFrac,minArea=W*H*T.minTileAreaFrac;
    var arranged=items.map(function(item){var selected=chooseAsset(item),data=selected&&selected.data,dims=data&&data.dimensions,mask=maskCells(data&&data.mask);if(!selected||!mask)return null;return{item:item,selected:selected,mask:mask,ar:dims&&dims[0]>0&&dims[1]>0?dims[0]/dims[1]:1.4,score:Math.pow(Math.max(1,+item.count||1),T.countExp)};}).filter(Boolean);
    var sumScore=arranged.reduce(function(a,t){return a+t.score;},0)||1;arranged.forEach(function(t){t.area=Math.max(minArea,budget*t.score/sumScore);});var sumArea=arranged.reduce(function(a,t){return a+t.area;},0);if(sumArea>budget){var fixed=arranged.filter(function(t){return t.area<=minArea+1e-9;}).reduce(function(a,t){return a+t.area;},0),flex=sumArea-fixed,scaleFlex=flex>0?Math.min(1,Math.max(0,budget-fixed)/flex):1;arranged.forEach(function(t){if(t.area>minArea+1e-9)t.area*=scaleFlex;});}arranged.forEach(function(t){t.fullW=Math.sqrt(t.area*t.ar);t.fullH=t.fullW/t.ar;if(exportMode){var px=t.selected.data.pixel_dimensions||t.selected.data.dimensions,quality=Math.min(px[0],px[1])<800?1:maxUpscale;t.nativeLimit=Math.min(px[0]*quality/t.fullW,px[1]*quality/t.fullH);if(t.nativeLimit<1){t.fullW*=t.nativeLimit;t.fullH*=t.nativeLimit;}t.maxW=px[0]*quality;t.maxH=px[1]*quality;}});
    var narrow=W<=700,xBias=narrow?1:T.ellipseAspectBias,yBias=narrow?1.7:1,pad=narrow?Math.max(1,COLLAGE_PAD-1):COLLAGE_PAD,placed=maskPack(arranged,W,H,xBias,yBias,pad);
    var b=bounds(placed);for(var iter=0;iter<10;iter++){var missing=placed.some(function(t){return t.x<-1000;}),overflow=b.L<0||b.T<0||b.R>W||b.B>H;if(!missing&&!overflow)break;var scale=.93;if(overflow){var sx=W*.96/Math.max(b.R-b.L,W*.96),sy=H*.94/Math.max(b.B-b.T,H*.94);scale=Math.min(scale,sx,sy);}arranged.forEach(function(t){t.fullW*=scale;t.fullH*=scale;});placed=maskPack(arranged,W,H,xBias,yBias,pad);b=bounds(placed);}
    if(!placed.some(function(t){return t.x<-1000;}))b=expandPacked(placed,W,H,Math.min(T.postScaleMax,exportMode?Math.min.apply(null,placed.map(function(t){return Math.min(t.maxW/t.fullW,t.maxH/t.fullH);})):T.postScaleMax),narrow,exportMode);
    if(isFinite(b.L)){var shiftX=W/2-(b.L+b.R)/2,shiftY=H/2-(b.T+b.B)/2;placed.forEach(function(t){if(t.x>-1000){t.x+=shiftX;t.y+=shiftY;}});}
    var missingItems=items.filter(function(item){return !arranged.some(function(t){return t.item===item&&t.x>-1000;});});
return {placed:placed.filter(function(t){return t.x>-1000;}),missing:missingItems};}
window.AvianCollage={arrange:arrange,edgePath:edgePath,chooseAsset:chooseAsset};
}());
