# %% 
#載入模型
import os
from keras.models import load_model
import numpy as np
import xarray as xr
import shutil
from dateutil import parser
import pandas as pd
from datetime import datetime, timedelta
ROOT = "./"
model = ROOT + "model_West_241220.h5"
model = load_model(model)

# %%
ref_avg_std = pd.read_csv('./ref_avg_std.csv', index_col=0)
def daily_and_normalization(ref_avg_std, df):
    for col in df.columns:
        if col == 'time':
            continue
        df[col] = ((df[col] - ref_avg_std[col]['mean']) / ref_avg_std[col]['std'])
    #Calculate daily stat
    #temperature_2m	relativehumidity_2m	precipitation	windspeed_10m	winddirection_10m	u	v
    df['time'] = pd.to_datetime(df['time'])
    df_daily_mean = df[['time', 'temperature_2m', 'relativehumidity_2m', 'precipitation', 'u', 'v']].resample('D', on='time').mean()
    df_daily_max = df[['time', 'temperature_2m', 'relativehumidity_2m', 'precipitation', 'u', 'v']].resample('D', on='time').max()
    df_daily_min = df[['time', 'temperature_2m', 'relativehumidity_2m', 'precipitation', 'u', 'v']].resample('D', on='time').min()
    #Append max, mean, min, to  columns name
    df_daily_mean.columns = [col + '_mean' for col in df_daily_mean.columns]
    df_daily_max.columns = [col + '_max' for col in df_daily_max.columns]
    df_daily_min.columns = [col + '_min' for col in df_daily_min.columns]
    df_daily = pd.concat([df_daily_mean,df_daily_max,df_daily_min],axis=1)
    #Re order columns
    df_daily = df_daily[[
        'temperature_2m_max',
        'temperature_2m_mean',
        'temperature_2m_min', 
        'relativehumidity_2m_max',
        'relativehumidity_2m_mean',
        'relativehumidity_2m_min',
        'precipitation_max', 
        'precipitation_mean',
        'precipitation_min', 
        'u_max',                  
        'u_mean',
        'u_min',
        'v_max',
        'v_mean',
        'v_min',
        ]]
    return df_daily
# %%
import pandas as pd

ERA5_archive = os.getenv("ERA5_INPUT_DIR", "../../ERA5")
skip = 0
x = []
x_metadata = []
for f in os.scandir(ERA5_archive):
    if skip >0:
        skip -= 1
        continue
    if f.name.endswith('.csv'):
        #12J990_口湖工作站_23.589978_120.180394.csv
        sta_no, sta_name, lat, lon = f.name.replace('.csv', '').split('_')
        print(f"Processing {f.name}")
        df_wea = pd.read_csv(f.path)
        #Keep only needed columns
        #temperature_2m	relativehumidity_2m		precipitation		windspeed_10m	winddirection_10m	Wu	Wv
        try:
            df_wea = df_wea[['time', 'temperature_2m', 'relativehumidity_2m', 'precipitation', 'Wu', 'Wv']]
            #Change Wu, Wv to u, v
            df_wea['u'] = df_wea['Wu']
            df_wea['v'] = df_wea['Wv']
            #Remove Wu, Wv
            df_wea = df_wea.drop(columns=['Wu', 'Wv'])
        except:
            pass
        df_daily = daily_and_normalization(ref_avg_std, df_wea)
        #做一個slide window，輸入資料是輸入資料是[(None, 19, 15)] ，所以每19天time step是一個資料
        for i in range(0, len(df_daily)-19):
            #站號,站名,日期,lat,lon
            x_metadata.append({"日期":df_daily.index[i+19]+timedelta(days=4), "lat":lat, "lon":lon, "站號":sta_no, "站名":sta_name})
            x.append(df_daily.iloc[i:i+19].values)
x = np.array(x)

# %%
#預測
y = model.predict(x)
# %%
#把預報結果存成各自的csv
results = []
for i in range(len(x_metadata)):
    #站號,站名,日期,lat,lon
    sta_no = x_metadata[i]["站號"]
    sta_name = x_metadata[i]["站名"]
    lat = x_metadata[i]["lat"]
    lon = x_metadata[i]["lon"]
    date = x_metadata[i]["日期"]
    prediction = str(round(y[i][0], 2))
    results.append([sta_no, sta_name, date, lat, lon, prediction])

# Group results by date
results_df = pd.DataFrame(results, columns=['站號', '站名', '日期', 'lat', 'lon', 'BlastLSTLS'])
for date, group in results_df.groupby('日期'):
    csv_path = f"../../rice_blast_prediction/data/{date.strftime('%Y%m%d')}_BlastLSTLS.csv"
    if os.path.exists(csv_path):
        existing_df = pd.read_csv(csv_path)
        combined_df = pd.concat([existing_df, group]).drop_duplicates(subset=['站號'], keep='last')
    else:
        combined_df = group
    #make sure date is in %Y-%m-%d format
    combined_df['日期'] = pd.to_datetime(combined_df['日期']).dt.strftime('%Y-%m-%d')
    print(f"{date} has {len(combined_df)} records")
    combined_df.to_csv(csv_path, index=False)
# %%
