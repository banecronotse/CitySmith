<?xml version="1.0" encoding="utf-8"?>
<CityModel xmlns="http://www.opengis.net/citygml/2.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:gml="http://www.opengis.net/gml" xmlns:gen="http://www.opengis.net/citygml/generics/2.0" xmlns:bldg="http://www.opengis.net/citygml/building/2.0">
	<cityObjectMember>
		<bldg:Building gml:id="B_MIX">
			<!-- main shell: flat roof at z=10, so eave = 10 -->
			<bldg:boundedBy>
				<bldg:RoofSurface>
					<bldg:lod3MultiSurface><gml:MultiSurface><gml:surfaceMember>
						<gml:Polygon gml:id="M_ROOF"><gml:exterior><gml:LinearRing>
							<gml:posList srsDimension="3">0 0 10 10 0 10 10 10 10 0 10 10 0 0 10</gml:posList>
						</gml:LinearRing></gml:exterior></gml:Polygon>
					</gml:surfaceMember></gml:MultiSurface></bldg:lod3MultiSurface>
				</bldg:RoofSurface>
			</bldg:boundedBy>
			<!-- balcony installation: body at z 4..5, below eave -->
			<bldg:outerBuildingInstallation>
				<bldg:BuildingInstallation>
					<bldg:boundedBy><bldg:WallSurface>
						<bldg:lod3MultiSurface><gml:MultiSurface><gml:surfaceMember>
							<gml:Polygon gml:id="BAL_W"><gml:exterior><gml:LinearRing>
								<gml:posList srsDimension="3">2 -1 4 4 -1 4 4 -1 5 2 -1 5 2 -1 4</gml:posList>
							</gml:LinearRing></gml:exterior></gml:Polygon>
						</gml:surfaceMember></gml:MultiSurface></bldg:lod3MultiSurface>
					</bldg:WallSurface></bldg:boundedBy>
				</bldg:BuildingInstallation>
			</bldg:outerBuildingInstallation>
			<!-- chimney installation: body at z 10..12, above eave -->
			<bldg:outerBuildingInstallation>
				<bldg:BuildingInstallation>
					<bldg:boundedBy><bldg:RoofSurface>
						<bldg:lod3MultiSurface><gml:MultiSurface><gml:surfaceMember>
							<gml:Polygon gml:id="CHM_R"><gml:exterior><gml:LinearRing>
								<gml:posList srsDimension="3">6 6 12 7 6 12 7 7 12 6 7 12 6 6 12</gml:posList>
							</gml:LinearRing></gml:exterior></gml:Polygon>
						</gml:surfaceMember></gml:MultiSurface></bldg:lod3MultiSurface>
					</bldg:RoofSurface></bldg:boundedBy>
					<bldg:boundedBy><bldg:WallSurface>
						<bldg:lod3MultiSurface><gml:MultiSurface><gml:surfaceMember>
							<gml:Polygon gml:id="CHM_W"><gml:exterior><gml:LinearRing>
								<gml:posList srsDimension="3">6 6 10 7 6 10 7 6 12 6 6 12 6 6 10</gml:posList>
							</gml:LinearRing></gml:exterior></gml:Polygon>
						</gml:surfaceMember></gml:MultiSurface></bldg:lod3MultiSurface>
					</bldg:WallSurface></bldg:boundedBy>
				</bldg:BuildingInstallation>
			</bldg:outerBuildingInstallation>
		</bldg:Building>
	</cityObjectMember>
</CityModel>
