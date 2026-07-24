<?xml version="1.0" encoding="utf-8"?>
<CityModel xmlns="http://www.opengis.net/citygml/2.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:gml="http://www.opengis.net/gml" xmlns:gen="http://www.opengis.net/citygml/generics/2.0" xmlns:bldg="http://www.opengis.net/citygml/building/2.0">
	<cityObjectMember>
		<bldg:Building gml:id="BOX1">
			<gen:stringAttribute name="note"><gen:value>test box</gen:value></gen:stringAttribute>
			<bldg:boundedBy>
				<bldg:GroundSurface>
					<bldg:lod3MultiSurface>
						<gml:MultiSurface>
							<gml:surfaceMember>
								<gml:Polygon gml:id="P_GROUND">
									<gml:exterior><gml:LinearRing gml:id="R_GROUND">
										<gml:posList srsDimension="3">0 0 0 10 0 0 10 10 0 0 10 0 0 0 0</gml:posList>
									</gml:LinearRing></gml:exterior>
								</gml:Polygon>
							</gml:surfaceMember>
						</gml:MultiSurface>
					</bldg:lod3MultiSurface>
				</bldg:GroundSurface>
			</bldg:boundedBy>
			<bldg:boundedBy>
				<bldg:RoofSurface>
					<bldg:lod3MultiSurface>
						<gml:MultiSurface>
							<gml:surfaceMember>
								<gml:Polygon gml:id="P_ROOF">
									<gml:exterior><gml:LinearRing gml:id="R_ROOF">
										<gml:posList srsDimension="3">0 0 3 10 0 3 10 10 3 0 10 3 0 0 3</gml:posList>
									</gml:LinearRing></gml:exterior>
								</gml:Polygon>
							</gml:surfaceMember>
						</gml:MultiSurface>
					</bldg:lod3MultiSurface>
				</bldg:RoofSurface>
			</bldg:boundedBy>
			<bldg:boundedBy>
				<bldg:WallSurface>
					<bldg:lod3MultiSurface>
						<gml:MultiSurface>
							<gml:surfaceMember>
								<gml:Polygon gml:id="P_WALL_S">
									<gml:exterior><gml:LinearRing gml:id="R_WALL_S">
										<gml:posList srsDimension="3">0 0 0 10 0 0 10 0 3 0 0 3 0 0 0</gml:posList>
									</gml:LinearRing></gml:exterior>
									<gml:interior><gml:LinearRing gml:id="R_WINDOW">
										<gml:posList srsDimension="3">3 0 1 7 0 1 7 0 2 3 0 2 3 0 1</gml:posList>
									</gml:LinearRing></gml:interior>
								</gml:Polygon>
							</gml:surfaceMember>
							<gml:surfaceMember>
								<gml:Polygon gml:id="P_WALL_E">
									<gml:exterior><gml:LinearRing gml:id="R_WALL_E">
										<gml:posList srsDimension="3">10 0 0 10 10 0 10 10 3 10 0 3 10 0 0</gml:posList>
									</gml:LinearRing></gml:exterior>
								</gml:Polygon>
							</gml:surfaceMember>
							<gml:surfaceMember>
								<gml:Polygon gml:id="P_WALL_N">
									<gml:exterior><gml:LinearRing gml:id="R_WALL_N">
										<gml:posList srsDimension="3">10 10 0 0 10 0 0 10 3 10 10 3 10 10 0</gml:posList>
									</gml:LinearRing></gml:exterior>
								</gml:Polygon>
							</gml:surfaceMember>
							<gml:surfaceMember>
								<gml:Polygon gml:id="P_WALL_W">
									<gml:exterior><gml:LinearRing gml:id="R_WALL_W">
										<gml:posList srsDimension="3">0 10 0 0 0 0 0 0 3 0 10 3 0 10 0</gml:posList>
									</gml:LinearRing></gml:exterior>
								</gml:Polygon>
							</gml:surfaceMember>
						</gml:MultiSurface>
					</bldg:lod3MultiSurface>
				</bldg:WallSurface>
			</bldg:boundedBy>
			<bldg:lod3Solid>
				<gml:Solid>
					<gml:exterior>
						<gml:CompositeSurface>
							<gml:surfaceMember xlink:href="#P_GROUND"/>
							<gml:surfaceMember xlink:href="#P_ROOF"/>
							<gml:surfaceMember xlink:href="#P_WALL_S"/>
							<gml:surfaceMember xlink:href="#P_WALL_E"/>
							<gml:surfaceMember xlink:href="#P_WALL_N"/>
							<gml:surfaceMember xlink:href="#P_WALL_W"/>
						</gml:CompositeSurface>
					</gml:exterior>
				</gml:Solid>
			</bldg:lod3Solid>
		</bldg:Building>
	</cityObjectMember>
</CityModel>
