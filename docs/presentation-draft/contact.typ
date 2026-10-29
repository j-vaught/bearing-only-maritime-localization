#set page(width:420mm,height:297mm,margin:9mm)
#set text(font:"Arial",size:10pt)
#let d=json("slides.json")
#let all=d.main+d.backup
#for start in range(0,all.len(),step:9) {
  grid(columns:(1fr,1fr,1fr),gutter:4mm,
    ..all.slice(start,calc.min(start+9,all.len())).enumerate().map(((j,s))=>[
      #let n=start+j+1
      #text(weight:"bold")[#s.number · #s.title]
      #linebreak()
      #let file="build/slides/slide-"+if n < 10 {"0"+str(n)}else{str(n)}+".png"
      #image(file,width:100%)
    ])
  )
  if start+9 < all.len() {pagebreak()}
}
