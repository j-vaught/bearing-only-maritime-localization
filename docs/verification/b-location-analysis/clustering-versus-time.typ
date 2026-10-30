#import "plot-common.typ": *
#set page(width:320mm,height:150mm,margin:0mm,fill:white)
#set text(font:"Arial",size:9pt)
#set document(author:"J.C. Vaught",title:"Detected aid spacing and angular coverage versus search time")
#let rows=json("aid-clustering.json").sites.filter(r=>r.detected_aid_count>15)
#place(top+left,canvas(length:1mm,{
  import draw: *
  rect((0,0),(320,150),fill:white,stroke:none)
  content((160,144),text(size:14pt)[Aid congregation and search time])
  content((160,133),key())
  let a=axes((20,26),(125,85),(0,750),(5,850),(0,150,300,450,600,750),(5,10,30,100,300,600),[Median nearest-aid distance (m)],[Search time (s), logarithmic],logy:true)
  let b=axes((178,26),(125,85),(90,360),(5,850),(90,135,180,225,270,315,360),(5,10,30,100,300,600),[Angular coverage around observer (degrees)],[Search time (s), logarithmic],logy:true)
  a.drawing
  b.drawing
  let offsets=(B2:(-4,6),B4:(-4,-4),B7:(5,0),B11:(6,0),B16:(6,0),B17:(5,-4),B18:(5,0))
  let right-offsets=(B2:(-4,6),B4:(7,3),B7:(5,0),B11:(6,0),B16:(6,0),B17:(-4,-4),B18:(5,0))
  for r in rows {
    let t=if r.status=="timeout" {600}else{r.search_s}
    let p=((a.x)(r.median_nearest_neighbor_distance_m),(a.y)(t))
    let q=((b.x)(r.noiseless_angular_occupied_span_deg),(b.y)(t))
    symbol(p,r.status)
    symbol(q,r.status)
    if r.site_id in offsets {
      for (point,offset) in ((p,offsets.at(r.site_id)),(q,right-offsets.at(r.site_id))) {
        let label=(point.at(0)+offset.at(0),point.at(1)+offset.at(1))
        line(point,label,stroke:0.3pt+rgb("#888888"))
        content(label,text(size:8pt)[#r.site_id])
      }
    }
    if r.status=="timeout" {
      for point in (p,q) {line((point.at(0),point.at(1)+1.5),(point.at(0),point.at(1)+5),stroke:0.7pt+red,mark:(end:"stealth",fill:red))}
    }
  }
  content((160,6),text(size:8pt)[Each point is one eligible site · Spacing uses detected aid representatives · Angular coverage is 360° minus the largest noiseless bearing gap])
}))
