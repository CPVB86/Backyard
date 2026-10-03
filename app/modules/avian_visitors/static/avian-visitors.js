(function () {
  "use strict";
  var FLY_PROB=0.15, poseBySpecies=Object.create(null), objectUrls=[], refreshTimer=null;
  var collage=document.getElementById("collage"), message=document.getElementById("message");
  var access=document.getElementById("access"), tokenInput=document.getElementById("token");

  function hash(text){var value=2166136261;for(var i=0;i<text.length;i++)value=Math.imul(value^text.charCodeAt(i),16777619);return value>>>0;}
  function maskCells(mask){
    if(!mask||!mask.bits||!mask.w||!mask.h)return null;
    try{var raw=atob(mask.bits),cells=[];for(var i=0;i<mask.w*mask.h;i++)if(raw.charCodeAt(i>>3)&(128>>(i&7)))cells.push([i%mask.w,Math.floor(i/mask.w)]);return cells.length?{w:mask.w,h:mask.h,cells:cells}:null;}catch(_){return null;}
  }
  function chooseAsset(item){
    var perched=item.assets&&item.assets.perched,flight=item.assets&&item.assets.flight,pose=poseBySpecies[item.scientific_name];
    if(!pose){pose=perched&&flight?(hash(item.scientific_name)%1000<FLY_PROB*1000?"flight":"perched"):perched?"perched":flight?"flight":null;poseBySpecies[item.scientific_name]=pose;}
    if(pose==="perched"&&!perched)pose=flight?"flight":null;if(pose==="flight"&&!flight)pose=perched?"perched":null;
    return pose?{pose:pose,data:item.assets[pose]}:null;
  }
  function tiles(items,width,height){
    var available=width*height*.62,weights=items.map(function(item){return Math.pow(item.count,.65);});
    var total=weights.reduce(function(a,b){return a+b;},0)||1;
    return items.map(function(item,index){
      var selected=chooseAsset(item),data=selected&&selected.data,dims=data&&data.dimensions;
      var ratio=dims&&dims[0]>0&&dims[1]>0?dims[0]/dims[1]:1,area=available*weights[index]/total;
      var w=Math.sqrt(area*ratio),h=w/ratio,cap=Math.min(width*.42,height*.58),scale=Math.min(1,cap/Math.max(w,h));
      return{item:item,selected:selected,mask:maskCells(data&&data.mask),w:Math.max(72,w*scale),h:Math.max(72,h*scale)};
    }).sort(function(a,b){return b.item.count-a.item.count||a.item.scientific_name.localeCompare(b.item.scientific_name);});
  }
  function place(all,width,height){
    var unit=5,gw=Math.ceil(width/unit),gh=Math.ceil(height/unit),occupied=new Uint8Array(gw*gh);
    function samples(tile,x,y){
      var out=[],mask=tile.mask;
      if(mask){var step=Math.max(1,Math.floor(mask.cells.length/1800));for(var i=0;i<mask.cells.length;i+=step)out.push([Math.floor((x+mask.cells[i][0]/mask.w*tile.w)/unit),Math.floor((y+mask.cells[i][1]/mask.h*tile.h)/unit)]);}
      else for(var yy=y;yy<y+tile.h;yy+=unit*2)for(var xx=x;xx<x+tile.w;xx+=unit*2)out.push([Math.floor(xx/unit),Math.floor(yy/unit)]);
      return out;
    }
    all.forEach(function(tile,order){
      var seed=hash(tile.item.scientific_name),angle=(seed%6283)/1000,found=null;
      for(var attempt=0;attempt<900;attempt++){
        var radius=3.1*Math.sqrt(attempt),a=angle+attempt*2.399963;
        var x=width/2-tile.w/2+Math.cos(a)*radius,y=height/2-tile.h/2+Math.sin(a)*radius*.72;
        if(x<0||y<0||x+tile.w>width||y+tile.h>height)continue;
        var pts=samples(tile,x,y),collision=false;
        for(var i=0;i<pts.length;i++){var px=pts[i][0],py=pts[i][1];if(px<0||py<0||px>=gw||py>=gh||occupied[py*gw+px]){collision=true;break;}}
        if(!collision){found={x:x,y:y,pts:pts};break;}
      }
      if(!found){var columns=Math.max(1,Math.floor(width/Math.max(tile.w,120))),fx=(order%columns)*width/columns,fy=Math.floor(order/columns)*Math.min(tile.h,height/3);found={x:Math.min(width-tile.w,Math.max(0,fx)),y:Math.min(height-tile.h,Math.max(0,fy)),pts:[]};}
      tile.x=found.x;tile.y=found.y;
      found.pts.forEach(function(p){for(var dy=-1;dy<=1;dy++)for(var dx=-1;dx<=1;dx++)if(p[0]+dx>=0&&p[1]+dy>=0&&p[0]+dx<gw&&p[1]+dy<gh)occupied[(p[1]+dy)*gw+p[0]+dx]=1;});
    });return all;
  }
  function edgePath(mask){
    if(!mask)return"M 8 18 Q 50 2 92 18";var tops=new Array(mask.w),i,x;
    for(i=0;i<mask.cells.length;i++){x=mask.cells[i][0];var y=mask.cells[i][1];if(tops[x]===undefined||y<tops[x])tops[x]=y;}
    var points=[];for(x=0;x<mask.w;x+=Math.max(1,Math.floor(mask.w/12)))if(tops[x]!==undefined)points.push([(x/mask.w)*100,Math.max(4,(tops[x]/mask.h)*100-3)]);
    return points.length<2?"M 8 18 Q 50 2 92 18":"M "+points.map(function(p){return p[0].toFixed(1)+" "+p[1].toFixed(1);}).join(" L ");
  }
  function loadImage(url,token){return fetch(url,{headers:{Authorization:"Bearer "+token}}).then(function(response){if(!response.ok)throw new Error("Afbeelding niet beschikbaar");return response.blob();}).then(function(blob){var objectUrl=URL.createObjectURL(blob);objectUrls.push(objectUrl);return objectUrl;});}
  function escapeHtml(value){var div=document.createElement("div");div.textContent=value;return div.innerHTML;}
  function draw(data,token){
    objectUrls.forEach(URL.revokeObjectURL);objectUrls=[];collage.replaceChildren();
    var rect=collage.getBoundingClientRect(),arranged=place(tiles(data.species,rect.width,rect.height),rect.width,rect.height);
    if(!arranged.length){message.textContent="Geen geaccepteerde vogelwaarnemingen in deze periode.";message.hidden=false;return;}message.hidden=true;
    arranged.forEach(function(tile,index){
      var el=document.createElement("article");el.className="bird"+(tile.selected?"":" missing");el.style.cssText="left:"+tile.x+"px;top:"+tile.y+"px;width:"+tile.w+"px;height:"+tile.h+"px";el.setAttribute("aria-label",tile.item.common_name+", "+tile.item.count+" waarnemingen");
      if(tile.selected){
        var img=document.createElement("img");img.alt="";el.appendChild(img);loadImage(tile.selected.data.url,token).then(function(src){img.src=src;}).catch(function(){el.classList.add("missing");img.remove();});
        var id="edge-"+index,svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("viewBox","0 0 100 100");
        svg.innerHTML='<defs><path id="'+id+'" d="'+edgePath(tile.mask)+'"/></defs><text style="font-size:'+Math.max(5,Math.min(10,150/tile.item.common_name.length))+'px"><textPath href="#'+id+'" startOffset="50%" text-anchor="middle">'+escapeHtml(tile.item.common_name)+'</textPath></text>';el.appendChild(svg);
      }else{var span=document.createElement("span");span.textContent=tile.item.common_name;el.appendChild(span);}
      collage.appendChild(el);setTimeout(function(){el.classList.add("entered");},40+index*55);
    });
  }
  function refresh(token){
    message.hidden=false;message.textContent="Vogels laden…";
    fetch("/api/avian-visitors/recent?hours=24&locale=nl",{headers:{Authorization:"Bearer "+token}}).then(function(response){if(response.status===401)throw new Error("Token niet geaccepteerd.");if(!response.ok)throw new Error("Backyard kon de collage niet laden.");return response.json();})
      .then(function(data){access.classList.add("connected");access.querySelector("button").textContent="Verversen";draw(data,token);clearTimeout(refreshTimer);refreshTimer=setTimeout(function(){refresh(token);},60000);})
      .catch(function(error){message.textContent=error.message;message.hidden=false;access.classList.remove("connected");sessionStorage.removeItem("backyardApiToken");});
  }
  access.addEventListener("submit",function(event){event.preventDefault();var token=tokenInput.value.trim()||sessionStorage.getItem("backyardApiToken");if(token){sessionStorage.setItem("backyardApiToken",token);refresh(token);}});
  var resizeTimer;window.addEventListener("resize",function(){clearTimeout(resizeTimer);resizeTimer=setTimeout(function(){var token=sessionStorage.getItem("backyardApiToken");if(token)refresh(token);},200);});
  var saved=sessionStorage.getItem("backyardApiToken");if(saved){tokenInput.value=saved;refresh(saved);}
}());
