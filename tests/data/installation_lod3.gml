<?xml version="1.0" encoding="utf-8"?>
<CityModel xmlns="http://www.opengis.net/citygml/2.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:gml="http://www.opengis.net/gml" xmlns:gen="http://www.opengis.net/citygml/generics/2.0" xmlns:bldg="http://www.opengis.net/citygml/building/2.0">
	<cityObjectMember>
		<bldg:Building gml:id="BLD_WITH_CHIMNEY">
			<bldg:outerBuildingInstallation>
				<bldg:BuildingInstallation>
					<bldg:boundedBy>
						<bldg:RoofSurface>
							<bldg:lod3MultiSurface>
								<gml:MultiSurface>
									<gml:surfaceMember>
										<gml:Polygon gml:id="CH_ROOF">
											<gml:exterior><gml:LinearRing>
												<gml:posList srsDimension="3">0 0 5 1 0 5 1 1 5 0 1 5 0 0 5</gml:posList>
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
										<gml:Polygon gml:id="CH_WALL">
											<gml:exterior><gml:LinearRing>
												<gml:posList srsDimension="3">0 0 3 1 0 3 1 0 5 0 0 5 0 0 3</gml:posList>
											</gml:LinearRing></gml:exterior>
										</gml:Polygon>
									</gml:surfaceMember>
								</gml:MultiSurface>
							</bldg:lod3MultiSurface>
						</bldg:WallSurface>
					</bldg:boundedBy>
				</bldg:BuildingInstallation>
			</bldg:outerBuildingInstallation>
		</bldg:Building>
	</cityObjectMember>
</CityModel>
