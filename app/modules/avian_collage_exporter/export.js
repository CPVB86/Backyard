/* Only the composition layers; no dashboard or interactive controls. */
window.renderExport=async function(snapshot){
  await document.fonts.ready;
  const root=document.getElementById("birds"),plan=AvianCollage.arrange(snapshot.species,root.clientWidth,root.clientHeight,{exportMode:true,maxUpscale:snapshot.max_upscale});
  if(plan.missing.length)throw new Error("Unrenderable species: "+plan.missing.map(x=>x.scientific_name).join(", "));
  for(const [index,tile] of plan.placed.entries()){
    const el=document.createElement("div");el.className="gtile";
    el.style.cssText=`left:${tile.x}px;top:${tile.y}px;width:${tile.fullW}px;height:${tile.fullH}px`;
    const img=new Image();img.src=tile.selected.data.url;el.appendChild(img);root.appendChild(el);
    await img.decode();
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("class","gtile-label");svg.setAttribute("viewBox","0 0 100 100");
    const defs=document.createElementNS(svg.namespaceURI,"defs"),path=document.createElementNS(svg.namespaceURI,"path");path.id="bird-edge-"+index;path.setAttribute("d",AvianCollage.edgePath(tile.mask));defs.appendChild(path);svg.appendChild(defs);
    const label=document.createElementNS(svg.namespaceURI,"text"),text=document.createElementNS(svg.namespaceURI,"textPath");
    text.setAttribute("href","#"+path.id);text.setAttribute("startOffset","50%");text.setAttribute("text-anchor","middle");text.textContent=tile.item.common_name;
    label.style.fontSize=Math.min(4.5,104/tile.item.common_name.length)+"px";label.appendChild(text);svg.appendChild(label);el.appendChild(svg);
  }
  document.getElementById("timestamp").textContent=snapshot.timestamp;
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  return {species:plan.placed.length,tiles:plan.placed.map(t=>({x:t.x,y:t.y,width:t.fullW,height:t.fullH,source:t.selected.data.pixel_dimensions}))};
};
