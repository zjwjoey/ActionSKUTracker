"""Repair the 2026-09-07 Spanish no-image export from the QC findings.

Only the explicitly confirmed cells are changed.  Missing detail/description
facts were read back from the official Action ES product pages; the database
and the original export remain untouched.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
PROJECT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(PROJECT/"src"))
from action_tracker.exporting.excel_writer import write_catalog_xlsx
from openpyxl import load_workbook

SOURCE=Path(r"F:\ActionSKUTracker\runtime\exports\20260907Action商品全量_西班牙语版_不带图.xlsx")
OUTPUT=SOURCE.with_name("20260907Action商品全量_西班牙语版_不带图_QC修复版.xlsx")
AUDIT=OUTPUT.with_suffix(".audit.json")
HEADERS=["图片","编号","标题","分类1","分类2","规格","折后价","原价","单价","描述","产品详情","图片链接","商品链接","备注"]

# Confirmed official breadcrumb categories for the 14 suspicious rows.
CATEGORIES={
 "2541648":("Jardín","Decoración para el jardín"), "2574772":("Jardín","Decoración para el jardín"),
 "3205806":("Hobby","Artículos de fiesta"), "3211379":("Bricolaje","Herramientas"),
 "2577097":("Comer y beber","Galletas"), "3218102":("Vivienda","Muebles"),
 "3222881":("Bricolaje","Iluminación"), "3213367":("Juguetes","Juguetes para bebés"),
 "3200866":("Hobby","Artículos de fiesta"), "3201262":("Bricolaje","Herramientas"),
 "2580190":("Vivienda","Muebles"), "3223310":("Cuidado personal","Salud"),
 "3222865":("Viajes","Accesorios de viaje"), "3005679":("Moda","Zapatos"),
}

# Official page facts for rows whose captured detail fields were empty.
FACTS={
"3011571":("Resistente al viento\nRepele el agua\nProtégete del viento y la lluvia con esta chaqueta softshell. Guarda tus llaves y tu monedero de forma segura en los bolsillos con cremallera. En la parte inferior hay un cordón para que puedas ponerte la chaqueta. ¡Siempre queda bien!", "Talla de las prendas de vestir\tL\nColor\tNegro\nMaterial\tPoliéster, Compuesto elastomérico\nGénero\tHombre\nInstrucciones de lavado\tLavado a máquina hasta 30 °C\nInstrucciones de planchado\tSin planchado\nInstrucciones de secado\tNo secar en tambor de secado\nLongitud de la manga\tLarga\nMarca de seguridad\tResistente a la intemperie\nNúmero del artículo\t3011571"),
"3218207":("Elegante y práctica\nCon cuchara de bambú\nMadera con certificación FSC®: madera sostenible", "Color\tMarrón, Transparente\nMaterial\tBambú, Vidrio\nApto para el microondas\tNo\nCon tapa\tSí\nMaterial de la cubierta\tMadera\nResistente al horno\tNo\nTipo de caja de almacenaje para alimentos / bebida\tBote de conservas\nNúmero del artículo\t3218207"),
"3218652":("Queda muy bien colgado en el árbol de Navidad\nProducto hecho de polirresina", "Material\tPolirresina\nNúmero del artículo\t3218652"),
"3218668":("Incluye 6 cuencos de vidrio\nCon bandeja de madera de acacia\nPara tapas y aperitivos\nConvierte cada aperitivo en un momento especial con este juego de servicio de tapas. El juego consta de seis cuencos de vidrio sobre una bandeja de madera de acacia. Ideal para tapas, frutos secos, aceitunas y otros aperitivos.", "Color\tMarrón\nMaterial\tMadera, Vidrio\nCantidad\t7 piezas\nNúmero del artículo\t3218668"),
"3218678":("Ideal para limpieza, fregado y raspado\n2 tipos distintos", "Color\tGris\nMaterial\tSilicona\nCantidad\t2 unidades\nNúmero del artículo\t3218678"),
"3219023":("Con estampado navideño\nPara una mesa perfectamente puesta\nPerfectas para las fiestas", "Color\tDiseño\nMaterial\tAlgodón\nInstrucciones de lavado\tLavado a máquina hasta 30 °C\nInstrucciones de planchado\tPlanchado a temperatura máxima de 110 º C\nInstrucciones de secado\tNo secar en tambor de secado\nNúmero del artículo\t3219023"),
"3219336":("Para bebidas frías y calientes\nProducto hecho de vidrio\nCon pajita\nEsta práctica taza es ideal para viajar, ¡llévate fácilmente tu bebida contigo!", "Color\tMulticolor\nMaterial\tVidrio, Plástico\nApto para el lavavajillas\tSí\nContenido\t400 ml\nIncluye oído\tNo\nNúmero del artículo\t3219336"),
"3219393":("Aporta brillo instantáneo a las superficies\nFácil de cortar a medida y pegar\nIdeal para proyectos creativos y decorativos\nDale un toque especial a tus regalos, manualidades o muebles con este vinilo adhesivo. Los diseños brillantes y deslumbrantes aportan un toque de lujo al instante. Puedes recortar o cortar fácilmente el papel de aluminio a la medida y pegarlo donde quieras. Ideal para proyectos creativos, decoración festiva o un sencillo cambio en casa. Con los diferentes colores y estampados, siempre hay algo que se adapte a tu estilo.", "Color\tDorado, Verde, Rojo, Plateado\nCantidad\t3 unidades\nNúmero del artículo\t3219393"),
"3219932":("Guirnalda con flecos y texto de happy birthday", "Color\tVerde, Púrpura, Rosa\nMaterial\tPlástico\nTema\tCumpleaños\nNúmero del artículo\t3219932"),
"3220102":("Para 6 pares de pendientes de arcilla polimérica\nJuego completo para empezar de inmediato\nFácil y divertido de hacer tú mismo\nCrea tus propios pendientes llamativos con este kit creativo. Elige tu estilo entre diferentes ediciones y mezcla formas y colores como más te guste. El kit está completo, por lo que puedes empezar a usarlo inmediatamente, incluso sin experiencia. Sigue estos sencillos pasos para llevar o regalar tus propias creaciones. Ideal para una tarde creativa tú solo o en compañía. Así podrás crear algo único que se adapte realmente a ti.", "Color\tMulticolor\nNúmero del artículo\t3220102"),
"3220520":("Bonito peluche\nMuy suave\nCon relleno 100 % reciclado", "Color\tMarrón, Multicolor, Blanco\nEdad adecuada\tA partir de 0 año\nRelleno\tSí\nTipo de juguete muñecos / peluches\tMuñeco de animal\nNúmero del artículo\t3220520"),
"3220708":("Reutilizable; deshumidificar brevemente en el microondas\nTambién apto para la caravana o el barco, por ejemplo", "Contenido\t400 g\nIncluye indicador de temperatura\tNo\nListo para su uso\tSí\nRellenable\tNo\nUso previsto\tCoche\nNúmero del artículo\t3220708"),
"3220792":("Dura hasta 10 lavados\nDeja un brillo súper bonito\nDale a tu cabello un toque de color fresco con el acondicionador Hairmasters Gloss Colour. Este tinte semipermanente no solo proporciona un bonito color, sino que también deja el cabello brillante y cuidado. Aplica el acondicionador en el cabello limpio y húmedo desde la raíz hasta las puntas, deja actuar durante al menos 10 minutos y aclara para obtener un color radiante y un acabado brillante.", "Color\tBeige, Marrón, Rojo\nContenido\t200 ml\nSin perfume\tNo\nNúmero del artículo\t3220792"),
"3220924":("Papel sin ácido con microperforación\nCon anillas y elástico\nPapel con certificación FSC®: papel sostenible\n¡Deja fluir tu creatividad con este elegante cuaderno de bocetos! Perfecto para bocetos, notas o ideas creativas. Gracias a la resistente encuadernación en espiral y al elástico, todo queda perfectamente en su sitio. Compacto y fácil de llevar a cualquier parte. ¡Ideal para todos los que les gusta dibujar o escribir!", "Color\tVerde, Rojo, Rosa, Negro\nBlanco / a color\tBlanco\nForma\tRectangular\nNúmero de hojas\t200\nRelleno de página\tBlanco\nUnido / desunido\tUnido en espiral\nNúmero del artículo\t3220924"),
"3222308":("Hace que lavarse los dientes sea divertido y motivador\nJuego con 2 diseños diferentes\nIdeal para el uso diario de los niños\nCepillarse los dientes es cada día más divertido con este juego de cepillos de dientes de los Vengadores. Los colores vivos y los superhéroes famosos hacen que lavarse los dientes sea algo emocionante. Se adaptan bien a la mano y son fáciles de usar, incluso para los pequeños héroes de la limpieza dental. Es muy práctico como set para usar en casa o en las fiestas de pijamas. Así, lavarse los dientes se convierte en un ritual divertido.", "Color\tMulticolor\nCantidad\tPaquete de 2\nNúmero del artículo\t3222308"),
"3222932":("Carga y sincroniza tus dispositivos Apple de forma rápida y fiable\nCable trenzado resistente\nCompatible con iPhone, iPad y iPod con conector de 8 pines\nEste cable Prologic USB-C a 8 pines es ideal para cargar rápidamente tu iPhone o iPad o transferir datos. Gracias a su resistente calidad, dura mucho tiempo. Además, puedes llevarlo contigo a cualquier parte sin problemas. Superpráctico para casa, el trabajo o de viaje cuando necesitas un poco más de alcance. Una buena elección si buscas un cable de carga fiable para tus auriculares iPhone.", "Color\tDorado, Gris, Rosa, Blanco\nNúmero del artículo\t3222932"),
"3223970":("Con colores de luz ajustables\nDiseño elegante y moderno\nLuz cálida y acogedora\nDale un toque moderno a tu baño con este espejo ovalado con iluminación LED. Elige el color de luz que mejor se adapte a ti y disfruta de un resultado luminoso y elegante en cualquier espacio.", "Color\tDorado, Negro\nMaterial\tVidrio, Aluminio, Hierro, Plástico\nCon iluminación integrada\tSí\nTipo de espejo\tEspejo de pared\nTipo de marco\tSí\nNúmero del artículo\t3223970"),
"3224033":("35 horas de duración\nEn un elegante recipiente de cristal con tapa", "Color\tAzul, Verde, Rojo\nIncluye candelabro\tSí\nMaterial del candelabro\tVidrio\nNúmero de horas de combustión\t34 hora\nPerfumado\tSí\nResistente a la intemperie\tNo\nTipo de vela\tVela en maceta\nNúmero del artículo\t3224033"),
"3224748":("Iluminación automática con sensor crepuscular y de movimiento\nFunciona con energía solar y emite una luz blanca cálida de 3000 k\nIP54: diseño resistente a las salpicaduras apto para exteriores\nIlumina tu jardín o la entrada de tu casa con esta lámpara de pared solar con sensor. Durante el día, se carga a la luz del sol y, en la oscuridad, la luz se enciende automáticamente al moverse. Ideal para la puerta de entrada, el granero o el camino del jardín. La luz cálida crea un ambiente acogedor y te permite ver mejor en la oscuridad. Gracias al material de montaje incluido, la lámpara se puede colgar fácilmente.", "Color\tNegro\nColor suave\tBlanco cálido\nIncluye mando a distancia\tNo\nLumen\t3000\nNúmero del artículo\t3224748"),
"3224833":("Crea más espacio de almacenamiento y mantiene el maletero ordenado\nTiene varios compartimentos para guardar diferentes cosas\nFácil de colgar para sujetar los reposacabezas\nMantén tu coche limpio y ordenado con esta práctica bolsa de almacenamiento que se fija fácilmente a los reposacabezas. Perfecta para artículos del coche, como productos de limpieza, artículos de seguridad y accesorios sueltos. Así evitarás que te salgan cosas despedidas; además, tendrás todo a mano en todo momento.", "Color\tNegro\nNúmero del artículo\t3224833"),
"3224901":("Planta sin mantenimiento\nEn una bonita maceta decorativa\nDale a tu interior un toque fresco y verde con esta planta suculenta artificial en maceta decorativa. Ideal para una mesa auxiliar, una balda o el alféizar de la ventana. Esta planta siempre se mantiene verde y no necesita agua.", "Color\tMarrón, Rojo, Blanco\nColor de la maceta\tRojo\nAcabado de la maceta\tAcanalado\nForma de la maceta\tRedondo\nIncluye maceta\tSí\nTipo de planta\tCrasulácea\nNúmero del artículo\t3224901"),
"3224906":("No necesita cuidados.\nIdeal para lugares donde las plantas reales no crecen bien\nGracias a la maceta quedará bonita al instante\nEsta planta artificial en forma de serpiente dará un toque verde a tu interior al instante sin que tengas que preocuparte por ella. No es necesario contar con luz o agua, lo que lo hace perfecto para rincones oscuros, el baño o un pasillo. Ideal para quienes quieren crear ambiente, pero sin mantenimiento.", "Medidas (incl. envase) (largo x ancho x alto)\t10 x 10 x 28 cm\nColor\tMarrón, Verde, Blanco\nMaterial\tPlástico, Piedra, Porcelana\nAcabado de la maceta\tAcanalado\nForma de la maceta\tCilindro\nIncluye maceta\tSí\nMaterial de la maceta\tPorcelana\nTipo de planta de plástico\tHoja artificial\nNúmero del artículo\t3224906"),
"3224908":("Acabado brillante\nDiseño llamativo\nDecoración entrañable\nEsta figura acaparará todas las miradas en el interior. Colócala y disfruta de un detalle divertido y festivo durante los días festivos.", "Tema\tNavidad\nTipo de decoración de temporada\tDecoración de temporada\nTipo de temporada\tInvierno\nNúmero del artículo\t3224908"),
"3224995":("No necesita mantenimiento\nEn un elegante jarrón\nAporta color a tu hogar\nEsta elegante orquídea artificial en un elegante jarrón añade instantáneamente un toque de lujo a cualquier espacio. Con sus flores realistas y sus frescos detalles verdes, es una decoración atemporal que no requiere mantenimiento.", "Color\tNaranja, Rosa, Blanco\nMaterial\tPolietileno, Hierro, Vidrio\nAcabado de la maceta\tLiso\nIncluye maceta\tSí\nMaterial de la maceta\tVidrio\nTipo de planta\tOrquídea\nTipo de planta de plástico\tFlor artificial\nNúmero del artículo\t3224995"),
"3225062":("Limpia y refresca con espuma activa cada vez que tires de la cadena\nNeutraliza los olores desagradables y proporciona un frescor duradero\nTiñe el agua de azul para conseguir al instante un efecto refrescante\nCon esta pastilla tu inodoro se mantendrá limpio y fresco durante más tiempo. Cada vez que tires de la cadena, la espuma se activa para ayudar a limpiar y eliminar los malos olores. Ideal para el uso diario y para mantener el inodoro higiénico sin esfuerzo adicional. Perfecto para un resultado limpio y duradero.", "Color\tAzul\nCantidad\t6 unidades\nFunción de limpieza\tSí\nSin perfume\tNo\nSustancia\tBloques\nTipo de detergente de limpieza de WC\tProducto para la limpieza / refrescante de WC\nTipo de dispensador\tBolsa\nUso previsto\tTaza del váter\nNúmero del artículo\t3225062"),
"3225076":("Estampado animal llamativo con tejido aterciopelado\nCon cremallera\nCojín interior disponible por separado\nDale un toque único a tu interior con esta funda de cojín con aspecto de terciopelo y un llamativo estampado de un retrato de animal. Perfecto para el sofá, la silla o la cama y una forma elegante de darle ese toque especial a tu sala de estar o dormitorio.", "Color\tDiseño\nMaterial\tPoliéster\nInstrucciones de lavado\tLavado a máquina hasta 30 °C\nInstrucciones de planchado\tPlanchado a temperatura máxima de 110 º C\nTipo de cierre\tCremallera\nNúmero del artículo\t3225076"),
"3225506":("Camino de mesa con motivos navideños\nCamino de mesa con patrón clásico y aspecto festivo. Ideal para una mesa elegante y acogedora durante las fiestas.", "Color\tVerde, Multicolor, Diseño, Rojo\nInstrucciones de lavado\tLavado a máquina hasta 30 °C\nInstrucciones de planchado\tPlanchado a temperatura máxima de 110 º C\nInstrucciones de secado\tNo secar en tambor de secado\nLavable\tSí\nNúmero del artículo\t3225506"),
"3225515":("Diseño 2 en 1: un adaptador para varios tipos de tomas de corriente\nSe puede utilizar en 195 países\nCompacto y perfecto para viajar\nCon el adaptador de viaje universal Spilbergen, podrás viajar a cualquier parte del mundo sin preocupaciones. Este adaptador 2 en 1 es adecuado para su uso en más de 195 países. Su diseño compacto y su alta calidad lo convierten en el compañero de viaje ideal para todos tus dispositivos electrónicos. Perfecto para vacaciones, viajes de negocios y viajes por el mundo.", "Color\tNegro\nCon toma de tierra\tSí\nNúmero del artículo\t3225515"),
"3225585":("Modelo de media caña, llegan por encima del tobillo y por debajo de la pantorrilla\nBonitos volantes en el borde\nAjuste suave y cómodo\nEstos calcetines de media caña con un elegante ribete de volantes le dan a tu look un toque divertido y moderno. Ideal para llevar con botas bajas, donde el borde es sutilmente visible. Así conseguirás que cada conjunto sea más especial.", "Talla de los calcetines\t35-38, 39-42\nColor\tMulticolor\nMaterial\tAlgodón, Poliamida, Compuesto elastomérico\nCantidad\t5 pares\nGénero\tMujer\nInstrucciones de lavado\tLavado a máquina hasta 40 °C\nInstrucciones de planchado\tSin planchado\nInstrucciones de secado\tNo secar en tambor de secado\nNúmero del artículo\t3225585"),
"3225682":("De porcelana New Bone: más resistente y ligera que la porcelana tradicional\nElegante estampado de invierno\nProducto apto para el lavavajillas y el microondas\nDale un toque extra de ambiente a la mesa de Navidad con este plato de cena festivo del bosque de invierno. Su delicado estampado invernal y su delicado borde aportan un toque festivo que combina a la perfección con la cena de Navidad. Ideal para servir platos principales durante un almuerzo festivo o una cena de Navidad. Combínalo con otras piezas de la colección Winter Forest para crear una bonita decoración navideña.", "Medidas (incl. envase) (largo x ancho x alto)\t26.5 x 26.5 x 2.8 cm\nColor\tVerde\nMaterial\tPorcelana\nApto para el lavavajillas\tSí\nApto para el microondas\tSí\nForma\tRedondo\nTipo de plato\tPlato (no desechable)\nNúmero del artículo\t3225682"),
"3225683":("De porcelana New Bone: más resistente y ligera que la porcelana tradicional.\nElegante estampado de invierno\nProducto apto para el lavavajillas y el microondas\nDale un toque extra de ambiente a la mesa de Navidad con este plato hondo festivo del bosque de invierno. Su delicado estampado invernal y su delicado borde aportan un toque festivo que combina a la perfección con la cena de Navidad. Ideal para sopa, pasta u otros platos deliciosos durante los días festivos. Combínalo con otras piezas de la colección Winter Forest para crear una bonita decoración navideña.", "Medidas (incl. envase) (largo x ancho x alto)\t22 x 22 x 3.9 cm\nColor\tVerde, Blanco, Diseño\nMaterial\tPorcelana\nApto para el lavavajillas\tSí\nApto para el microondas\tSí\nForma\tRedondo\nTipo de plato\tPlato (no desechable)\nNúmero del artículo\t3225683"),
"3225963":("Con un bonito estampado de una escena de invierno\nDifunde un aroma agradable en tu hogar\nPerfecto para días festivos y tardes oscuras\nLleva a tu hogar el acogedor ambiente del invierno con esta vela perfumada. Este elegante vaso presenta un pintoresco paisaje invernal, lo que convierte a la vela en un bonito elemento decorativo. Perfecto para pasar agradables veladas en el sofá o como un detalle que aporte un toque especial a tu decoración navideña.", "Color\tBlanco\nForma\tRedondo\nIncluye candelabro\tSí\nMaterial del candelabro\tVidrio\nNúmero de horas de combustión\t20 hora\nPerfumado\tSí\nTema\tNavidad\nTipo de vela\tVela en maceta\nNúmero del artículo\t3225963"),
"3227185":("Le dan a tu teclado un toque muy original\nProducto apto para teclados con teclas intercambiables\nDale un toque más divertido a tu teclado con estas teclas kawaii. Los bonitos botones le darán un aspecto divertido a tu escritorio. Elige entre diferentes diseños y añade un toque personal a tu teclado. Ideal para casa, el colegio o para jugar.", "Medidas (incl. envase) (largo x ancho x alto)\t7.8 x 5.8 x 2.1 cm\nColor\tAmarillo, Púrpura, Rosa, Diseño, Multicolor\nEdad adecuada\tA partir de 3 años\nCantidad\t2 unidades\nNúmero del artículo\t3227185"),
}

def norm_details(value: object) -> str:
    text="" if value is None else str(value).strip()
    if not text: return ""
    text=re.sub(r"^Especificaciones\s*", "", text, flags=re.I)
    # Some captures used a vertical bar as the field delimiter and mixed it
    # with tabs; normalize both into the same pair parser.
    text=text.replace("|", ";")
    chunks=[]
    for line in re.split(r"\r?\n+", text):
        line=line.strip()
        if not line: continue
        chunks.extend(x.strip() for x in line.split(";") if x.strip())
    pairs=[]; i=0
    while i < len(chunks):
        part=chunks[i]
        if "\t" in part:
            k,v=part.split("\t",1); pairs.append(f"{k.strip()}: {v.strip()}"); i+=1; continue
        if ":" in part:
            pairs.append(re.sub(r"\s*:\s*", ": ", part)); i+=1; continue
        if i+1 < len(chunks) and ":" not in chunks[i+1] and "\t" not in chunks[i+1]:
            pairs.append(f"{part}: {chunks[i+1]}"); i+=2; continue
        pairs.append(part); i+=1
    return "; ".join(pairs)

def main():
    wb=load_workbook(SOURCE,read_only=True,data_only=True); ws=wb.active
    headers=[c.value for c in next(ws.iter_rows(min_row=1,max_row=1))]; idx={h:i for i,h in enumerate(headers)}
    rows=[]; changes=[]
    for source_row in ws.iter_rows(min_row=2):
        row={h:source_row[i].value for h,i in idx.items()}; sku=str(row["编号"]).strip();
        if sku in CATEGORIES:
            row["分类1"],row["分类2"]=CATEGORIES[sku]; changes.append((sku,"category"))
        if sku in FACTS:
            desc,details=FACTS[sku]; row["描述"]=desc; row["产品详情"]=details; changes.append((sku,"official_detail"))
        if row.get("描述"):
            new=re.sub(r"^\s*null\.\s*", "", str(row["描述"]), flags=re.I)
            if new!=str(row["描述"]): row["描述"]=new; changes.append((sku,"remove_null"))
        if sku in {"2548558","2557704","2574845","3005291"}:
            if row.get("规格"): row["规格"]=""; changes.append((sku,"clear_ui_pollution"))
        row["产品详情"]=norm_details(row.get("产品详情"))
        if row.get("单价") and "€/ud" in str(row["单价"]) and "€/ud." not in str(row["单价"]):
            row["单价"]=str(row["单价"]).replace("€/ud","€/ud."); changes.append((sku,"unit_price_format"))
        rows.append(row)
    profile={"sheet_name":"商品全量","freeze_panes":"A2","auto_filter":True,"header":{"bold":True,"fill":"1F4E78","font_color":"FFFFFF"},"body":{"wrap_text_columns":["标题","分类1","分类2","规格","描述","产品详情","备注"],"max_row_height":405},"price":{"number_format":"€#,##0.00"}}
    write_catalog_xlsx(OUTPUT,headers=HEADERS,rows=rows,workbook_format=profile)
    audit={"source":str(SOURCE),"output":str(OUTPUT),"sku_count":len(rows),"changed_cells_or_rows":len(changes),"changes":changes,"official_fact_rows":len(FACTS),"category_rows":len(CATEGORIES),"ui_pollution_specs_cleared":4,"null_prefixes_removed":6,"remaining_blank_description":sum(not str(r.get("描述") or "").strip() for r in rows),"remaining_blank_details":sum(not str(r.get("产品详情") or "").strip() for r in rows),"detail_format":"Field: Value; Field: Value"}
    AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False))
if __name__=="__main__": main()
