# %%
import pandas as pd
#Suppress SettingWithCopyWarning
pd.options.mode.chained_assignment = None
import numpy as np
from tensorflow.keras.models import load_model
from tensorflow.keras import backend as K
import tensorflow_addons as tfa
from requests import get
from matplotlib import pyplot as plt
from datetime import datetime, timedelta
import os
ranger2 = tfa.optimizers.Lookahead(tfa.optimizers.RectifiedAdam(learning_rate=0.001))
ROOT = "./"

# %%
"""
def open_meteo_archive (latitude = "24.145736", longitude = "120.684075", start_date="2023-01-01", end_date="2023-01-25"):
    #URI =f"https://archive-api.open-meteo.com/v1/archive?latitude={latitude}&longitude={longitude}&start_date={start_date}&end_date={end_date}&hourly=temperature_2m,relativehumidity_2m,dewpoint_2m,apparent_temperature,precipitation,weathercode,cloudcover,direct_radiation&models=best_match&timezone=Asia%2FSingapore"
    URI=f"https://archive-api.open-meteo.com/v1/archive?latitude={latitude}&longitude={longitude}&start_date={start_date}&end_date={end_date}&hourly=temperature_2m,relativehumidity_2m,dewpoint_2m,precipitation,cloudcover,direct_radiation,windspeed_10m,winddirection_10m&models=best_match&timezone=Asia%2FSingapore"
    response = get(URI).json()
    try:
        om_df = pd.DataFrame(response['hourly'])
    except Exception as e:
        print(e, response)
    #Calculate wind vector
    radian = om_df ['winddirection_10m'] * (np.pi/180)
    om_df ['Wu'] = - om_df ['windspeed_10m'] * np.sin(radian)
    om_df ['Wv'] = - om_df ['windspeed_10m'] * np.cos(radian)
    om_df['time'] = pd.to_datetime(om_df['time'])
    om_df.set_index('time', inplace=True)
    om_df.dropna(inplace=True)
    return om_df
def open_meteo_forecast(latitude = "24.145736", longitude = "120.684075", past_days = 14):
    URI=f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&hourly=temperature_2m,relativehumidity_2m,dewpoint_2m,precipitation,cloudcover,direct_radiation,windspeed_10m,winddirection_10m&current_weather=true&past_days={past_days}&timezone=Asia%2FSingapore"
    response = get(URI).json()
    om_df = pd.DataFrame(response['hourly'])
    #Calculate wind vector
    radian = om_df ['winddirection_10m'] * (np.pi/180)
    om_df ['Wu'] = - om_df ['windspeed_10m'] * np.sin(radian)
    om_df ['Wv'] = - om_df ['windspeed_10m'] * np.cos(radian)
    om_df['time'] = pd.to_datetime(om_df['time'])
    om_df.set_index('time', inplace=True)
    return om_df
def archive_and_forecast(latitude = "24.145736", longitude = "120.684075", start_date="2023-01-01"):
    archive = open_meteo_archive(latitude, longitude, start_date, end_date = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d"))
    forecast = open_meteo_forecast(latitude, longitude, past_days = 14)
    total = pd.concat([archive, forecast])
    total[~total.index.duplicated(keep='first')]
    return total
"""
def extract_window(df, window_size):
    X = []
    for i in range(len(df) - window_size +1):
        yield df[i: i + window_size]


# %%
"""
stations = pd.read_csv("https://raw.githubusercontent.com/Raingel/weather_station_list/main/data/weather_sta_list.csv")
stations = stations[stations["撤站日期"].isnull()]
stations = stations[stations["海拔高度(m)"]<500]
"""
# %%
X = []
info = []
ERA5_INPUT_DIR = os.getenv("ERA5_INPUT_DIR", "../../ERA5/")
for f in os.scandir(ERA5_INPUT_DIR):
    if f.name.endswith(".csv"):
        print(f.name)
        try:
            sta_id, sta_name, lat, lon= f.name.replace(".csv", "").split("_")
            weaDf = pd.read_csv(f.path)
        except Exception as e:
            print(e)
            continue
        weaDf['time'] = pd.to_datetime(weaDf['time'])
        weaDf.set_index('time', inplace=True)
        weaDf_d = weaDf.resample('D').agg(['max', 'mean', 'min'])
        weaDf_d.columns = ['_'.join(col).strip() for col in weaDf_d.columns.values]
        selCols = [
            #'temperature_2m_max', 
            'temperature_2m_mean', 
            #'temperature_2m_min',
            'relativehumidity_2m_max',
            #'relativehumidity_2m_mean',
            #'relativehumidity_2m_min',
            'precipitation_mean',
            #'cloudcover_mean',
            #'direct_radiation_mean',
            'u_mean',
            'v_mean',
            #'soil_temperature_0_to_7cm_mean',
            #'soil_moisture_0_to_7cm_mean',
            ]
        input = weaDf_d[selCols]
        input['temp_norm'] = (input['temperature_2m_mean'] - 9.9625) / 21.016
        input['rh_norm'] = (input['relativehumidity_2m_max'] - 57.0) / 43.0
        input['precip_norm'] = (input['precipitation_mean'] - 0.0) / 6.274
        input['u_norm'] = (input['u_mean'] - -45.732) / 66.011
        input['v_norm'] = (input['v_mean'] - -40.008) / 63.111
        for e in extract_window(input, 20):
            x = e[['temp_norm', 'rh_norm', 'precip_norm', 'u_norm', 'v_norm']].to_numpy()
            X.append(x)
            info.append({"date":e.iloc[-1].name + pd.Timedelta(days=3), "sta_id":sta_id,"sta_name":sta_name, "lat":lat, "lon":lon})

# %%
print("X shape:", np.array(X).shape, "info shape:", np.array(info).shape)

# %%
model = load_model(ROOT+"/230126.h5")
pre = model.predict(np.array(X))[:,0]

# %%
predict_result = pd.DataFrame({ "站號": [i["sta_id"] for i in info],"站名": [i["sta_name"] for i in info],"日期": [i["date"] for i in info], "lat": [i["lat"] for i in info], "lon": [i["lon"] for i in info], "BlastGRU-TW":pre})

#Save separately by 日期
for date in predict_result["日期"].unique():
    #date To string
    ds = date.strftime("%Y%m%d")
    predict_result[predict_result["日期"]==date].to_csv("../../rice_blast_prediction/data/"+ds+"_BlastGRU-TW.csv", index=False)
    print(f"Save {ds}_BlastGRU-TW.csv")
# %%
""""
import mysql.connector
#連線資料
db = mysql.connector.connect(
  host="localhost",
  user="blast_forecast",
  password="pqExHT91OT9rq6QG",
  database="blast_forecast"
)
mycursor = db.cursor()
for index, row in predict_result.iterrows():
    print("Writing", row['站號'], row['日期'], row['lat'], row['lon'], row['BlastGRU-TW'])
    sql = "INSERT INTO `blast_forecast` (`站號`, `日期`, `lat`, `lon`, `BlastGRU-TW`) VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE `BlastGRU-TW` = %s"
    val = (row['站號'], row['日期'], row['lat'], row['lon'], row['BlastGRU-TW'], row['BlastGRU-TW'])
    mycursor.execute(sql, val)
db.commit()
"""
# %%



