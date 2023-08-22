from flask import Flask, render_template, request, flash, redirect
from flask_mail import Message, Mail
import folium
from folium.plugins import FastMarkerCluster
import datetime
import matplotlib.cm as cm
import matplotlib.colors as colors
from branca.element import Template, MacroElement
from pymongo import MongoClient
from forms import ContactForm
import geopandas as gpd
import fiona
import json
from jinja2 import Template
from playwright.async_api import async_playwright
import os
import zipfile
import uuid
import shutil
import tempfile

mail = Mail()

app = Flask(__name__)
client = MongoClient("mongodb://localhost:27017/")

app.secret_key = '****************'

app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 465
app.config["MAIL_USE_SSL"] = True
app.config["MAIL_USERNAME"] = 'your_email@gmail.com'
app.config["MAIL_PASSWORD"] = '****************' # password generated in Google Account Settings under 'Security', 'App passwords',
                                                 # choose 'other' in the app menu, create a name (here: 'FlaskMail'),
                                                 # and generate password. The password has 16 characters.
                                                 # Copy/paste it under app.config["MAIL_PASSWORD"].
                                                 # It will give you access to your gmail when you have two steps verification.
mail.init_app(app)


def load_dict_from_text(filename):
    with open(filename, 'r') as f:
        content = f.read()
        return json.loads(content)

async def make_view(i):
    # Generate a unique ID for this request
    unique_id = str(uuid.uuid4())

    # Create a temporary directory for this request
    with tempfile.TemporaryDirectory(prefix=f"temp_{unique_id}_") as extraction_dir:

        async with async_playwright() as p:
            # ... Same as before ...
            browser = await p.chromium.launch(headless=True)
            address = 'https://mft.rrc.texas.gov/link/d551fb20-442e-4b67-84fa-ac3f23ecabb4'
            page = await browser.new_page()
            await page.goto(address)
            handle = page.locator("//*[@id='fileTable_paginator_bottom']/select")
            await handle.select_option("1000")

            async with page.expect_download() as download_handler:
                await page.locator(f'#fileTable\:{i}\:j_id_2d').click()
            download = await download_handler.value
            temp_file_path = await download.path()

            # Define a path for the unique ZIP file within the temporary directory
            zip_file_path = os.path.join(extraction_dir, f'{unique_id}.zip')

            # Copy the temporary file to the unique ZIP path
            shutil.copy(temp_file_path, zip_file_path)

            # Open and extract ZIP file
            with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
                zip_ref.extractall(extraction_dir)

            # Assume the shapefile is the only file with .shp extension in the extracted directory
            shapefile_path = next(os.path.join(extraction_dir, f) for f in os.listdir(extraction_dir) if f.endswith('.shp'))

            # Read the shapefile using Fiona
            with fiona.open(shapefile_path) as src:
                gdf = gpd.GeoDataFrame.from_features(src, crs=src.crs)

            await browser.close()
            return gdf

@app.context_processor
def inject_today_date():
    return {'year': datetime.date.today().year}


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    form = ContactForm()

    if request.method == 'POST':
        if form.validate() == False:
            flash('All fields are required.')
            return render_template('contact.html', form=form)
        else:
            msg = Message(form.subject.data, sender='contact@example.com', recipients=['your_email@gmail.com'])
            msg.body = """
            From: %s <%s>
            %s
            """ % (form.name.data, form.email.data, form.message.data)
            mail.send(msg)
            return render_template('contact.html', success=True)

    elif request.method == 'GET':
        return render_template('contact.html', form=form)

@app.route('/map1')
def well_mapf():
    db = client['local']
    colln = db['geodata']
    cursor = list(colln.find())
    geojson_data = {
        "type": "FeatureCollection",
        "features": cursor[0]['features']
    }
    gdf = gpd.GeoDataFrame.from_features(geojson_data)
    gdf.crs = 'EPSG:4267'
    gdf = gdf.to_crs('4326')
    m = folium.Map(location=[gdf.geometry.centroid.y.mean(), gdf.geometry.centroid.x.mean()], zoom_start=10)
    geojson_data = gdf.to_json()
    js_function = f"""
    function onEachFeature(feature, layer) {{
        layer.on('click', function (e) {{
            var fid = feature.properties.FID;
            var district = feature.properties.district;
            var url = "/other_map?FID=" + fid + "&district=" + district;
            window.location.href = url;
        }});
    }}

    var geojson_data = {geojson_data};

    L.geoJson(geojson_data, {{
        onEachFeature: onEachFeature
    }}).addTo({m.get_name()});
    """

    # Map the unique districts to integers
    unique_districts = gdf['district'].unique()
    district_mapping = {district: i for i, district in enumerate(unique_districts)}

    # Create a colormap
    colormap = cm.get_cmap('tab20', len(unique_districts))

    # Define a style function that returns the color for a given feature
    def style_function(row):
        district = row['district']
        idx = district_mapping[district]
        color = colors.to_hex(colormap(idx / len(unique_districts)))
        return {
            'fillColor': color,
            'fillOpacity': 0.7,
            'weight': 2,
            'color': 'black',
        }

    # Mapping of district names to their corresponding colors
    district_color_mapping = {district: colors.to_hex(colormap(district_mapping[district] / len(unique_districts))) for
                              district in unique_districts}

    template = """
    {% macro html(this, kwargs) %}
    <div style="position: fixed; bottom: 50px; left: 50px; width: 200px; height: 100px; z-index:9999; font-size:9px; background-color: rgba(255, 255, 255, 0.8); padding: 10px; border-radius: 10px;">
        <p><strong>District Legend</strong></p>
        {% for value, key in this.mapping.items() %}
        <span style="color:{{ key }};">█</span> {{ value }}<br>
        {% endfor %}
    </div>
    <div style="position: fixed; bottom: 0px; left: 0px; width: 100%; height: 0px; z-index:9998;"></div>
    {% endmacro %}
    """
    # Create a MacroElement that adds the legend to the map
    class DistrictLegend(MacroElement):
        def __init__(self, mapping):
            super(DistrictLegend, self).__init__()
            self._template = Template(template)
            self.mapping = mapping

    # Add the legend to the map
    legend = DistrictLegend(mapping=district_color_mapping)

    m.add_child(legend)
    geojson_objects = [
        folium.GeoJson(
            data={
                'type': 'Feature',
                'properties': {
                    'FID': row['FID'],
                    'district': row['district']
                },
                'geometry': row['geometry'].__geo_interface__,
            },
            style_function=lambda feature, row=row: style_function(row),
            tooltip=folium.GeoJsonTooltip(fields=['FID', 'district']),

            popup=folium.Popup(
                html=f'<a href="/other_map?FID={row["FID"]}&district={row["district"]}" target="_blank">Click for Well Map</a>'))

        for index, row in gdf.iterrows()
    ]

    for geojson in geojson_objects:
        geojson.add_to(m)
    return render_template('texas.html',map=m._repr_html_())

@app.route('/other_map')
async def other_map():

    dist = request.args.get('district')
    fid = request.args.get('FID')
    comb_filt_dict = load_dict_from_text('static/files/newcombfilt.txt')
    map_idx = load_dict_from_text('static/files/idxtofile.txt')
    str_build = f"{(fid)} {dist}"
    map_rev_idx = {v:k for k,v in map_idx.items()}
    file_need = map_rev_idx[comb_filt_dict[str_build][0]]
    gdf = await make_view(file_need)
    gdf.crs = 'EPSG:4267'
    gdf = gdf.to_crs('4326')
    m = folium.Map(location=[gdf.geometry.centroid.y.mean(), gdf.geometry.centroid.x.mean()], zoom_start=10)
    folium.GeoJson(gdf).add_to(m)
    marker_data = gdf[['geometry', 'API', 'SURFACE_ID']].copy()
    marker_data['lat'] = marker_data['geometry'].apply(lambda geom: geom.y)
    marker_data['lng'] = marker_data['geometry'].apply(lambda geom: geom.x)
    marker_values = marker_data[['lat', 'lng', 'API', 'SURFACE_ID']].values.tolist()

    """ defining parameters for our markers and the popups when clicking on single markers """
    callback = ('function (row) {'
                'var marker = L.marker(new L.LatLng(row[0], row[1]));'
                'var icon = L.AwesomeMarkers.icon({'
                "icon: 'star',"
                "iconColor: 'black',"
                "markerColor: 'lightgray',"
                '});'
                'marker.setIcon(icon);'
                "var popup = L.popup({maxWidth: '300'});"
                "const display_text = {text: '<b>API: </b>' + row[2] + '</br>' + '<b> SURFACE_ID: </b>' + row[3]};"
                "var mytext = $(`<div id='mytext' class='display_text' style='width: 100.0%; height: 100.0%;'> ${display_text.text}</div>`)[0];"
                "popup.setContent(mytext);"
                "marker.bindPopup(popup);"
                'return marker};')

    """ creating clusters with FastMarkerCluster """
    fmc = FastMarkerCluster(marker_values, callback=callback)
    fmc.layer_name = 'Dev Wells'
    m.add_child(fmc)
    folium.LayerControl().add_to(m)
    return render_template('othermap.html', map=m._repr_html_())
if __name__ == '__main__':
    # app.run(debug=True)
    app.run("0.0.0.0", port=80, debug=False) # added host parameters for docker container