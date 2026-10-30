#import "plot-common.typ": *
#set page(width:320mm,height:150mm,margin:0mm,fill:white)
#set text(font:"Arial",size:9pt)
#set document(author:"J.C. Vaught",title:"Actual detected charting aid positions relative to observer")
#let sites=json("aid-clustering.json").sites
#place(top+left,canvas(length:1mm,{
  import draw: *
  rect((0,0),(320,150),fill:white,stroke:none)
  content((160,144),text(size:14pt)[Detected charting aid positions relative to the boat])
  content((160,135),text(size:9pt)[Blue squares mark detected aid representatives · Black × marks the simulated boat · Common scale in every panel])
  for (i,id) in ("B2","B4","B17").enumerate() {
    let r=sites.find(s=>s.site_id==id)
    let ox=17+i*106
    let a=axes((ox,36),(84,84),(-10,10),(-10,10),(-10,-5,0,5,10),(-10,-5,0,5,10),[East from boat (km)],[North from boat (km)])
    a.drawing
    circle(((a.x)(0),(a.y)(0)),radius:42,fill:none,stroke:(paint:rgb("#888888"),thickness:0.45pt,dash:"dashed"))
    for point in r.detected_aid_report_origins {
      let p=((a.x)(point.local_east_m/1000),(a.y)(point.local_north_m/1000))
      rect((p.at(0)-0.65,p.at(1)-0.65),(p.at(0)+0.65,p.at(1)+0.65),fill:blue,stroke:0.25pt+white)
    }
    let x=(a.x)(0)
    let y=(a.y)(0)
    for (weight,col) in ((2.2pt,white),(0.9pt,black)) {
      line((x - 1.2,y - 1.2),(x+1.2,y+1.2),stroke:weight+col)
      line((x - 1.2,y+1.2),(x+1.2,y - 1.2),stroke:weight+col)
    }
    content((ox+42,19),text(size:10pt)[#r.site_id · #r.port])
    content((ox+42,12),text(size:9pt)[#r.detected_aid_count aids · median nearest neighbor #calc.round(r.median_nearest_neighbor_distance_m) m])
    content((ox+42,5),text(size:8pt)[Angular coverage #calc.round(r.noiseless_angular_occupied_span_deg)° · #r.member_pairs_below_1cm near-coincident member pairs])
  }
}))
