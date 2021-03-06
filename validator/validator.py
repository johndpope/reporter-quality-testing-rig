from __future__ import division
import requests
import time as t
from shapely.geometry import LineString, MultiPoint, MultiLineString
import numpy as np
import json
import pandas as pd
from random import shuffle
from geojson import Feature, FeatureCollection
import itertools
from pyproj import Proj, transform
from scipy.stats import norm
from ipywidgets import Layout
from ipyleaflet import (
    Map,
    TileLayer,
    Circle,
    GeoJSON
)
from matplotlib import pyplot as plt


def get_route_metrics(routeList, sampleRates, noiseLevels,
                      turnPenaltyFactor=500,
                      saveResults=True):

    distance_metrics = [
        'segments', 'distance traveled', 'undermatches',
        'undermatch distance', 'overmatches', 'overmatch distance']

    speed_metrics = [
        'edge_speed_error', 'pct_edges_too_fast', 'pct_edges_too_slow',
        'segment_speed_error', 'pct_segments_too_fast',
        'pct_segments_too_slow', 'segment_speed_error_matched',
        'segment_speed_error_missed']

    scoring_metrics = distance_metrics + speed_metrics

    df = pd.DataFrame(columns=[
        'route', 'noise', 'sample_rate', 'avg_density'] +
        scoring_metrics + ['route_url', 'trace_attr_url', 'reporter_url'])

    speedDf = pd.DataFrame(columns=[
        'route_name', 'segment_id', 'sample_rate', 'noise', 'pct_error',
        'matched'])
    outDfRow = -1
    tpf = turnPenaltyFactor

    for i, rteCoords in enumerate(routeList):

        stName = rteCoords[0].keys()[0].encode("ascii", "ignore")
        endName = rteCoords[1].keys()[0].encode("ascii", "ignore")
        routeName = '{0}_to_{1}'.format(stName, endName)
        shape, routeUrl = get_route_shape(rteCoords)
        if shape is None:
            print routeUrl
            continue
        edges, shapeCoords, traceAttrUrl = get_trace_attrs(
            shape, shapeMatch="map_snap", turnPenaltyFactor=tpf)
        edges = get_coords_per_second(shapeCoords, edges, '2768')
        avgDensity = np.mean([edge['density'] for edge in edges])

        for noise in noiseLevels:
            noise = round(noise, 3)

            for sampleRate in sampleRates:
                print(
                    "Route: {0} // Noise Level: "
                    "{1} // Sample Rate: {2}".format(
                        i, noise, sampleRate))
                Hz = round(1 / sampleRate, 3)
                outDfRow += 1
                df.loc[
                    outDfRow, [
                        'route', 'noise', 'sample_rate',
                        'route_url', 'trace_attr_url']] = [
                            routeName, noise, sampleRate, routeUrl,
                            traceAttrUrl]
                dfEdges = format_edge_df(edges)
                if dfEdges['num_segments'].max() > 1:
                    break
                dfEdges, jsonDict, geojson, gpsMatchEdges = synthesize_gps(
                    dfEdges, shapeCoords, '2768', noise=noise,
                    sampleRate=sampleRate, turnPenaltyFactor=tpf)

                if jsonDict is None or geojson is None:
                    msg = "Trace attributes tried to call more" + \
                        " edges than are present in the route shape".format(
                            routeName)
                    df.loc[outDfRow, scoring_metrics + ['reporter_url']] = \
                        [None] * 6 + [msg]
                    continue
                segments, reportUrl = get_reporter_segments(jsonDict)
                if segments is None:
                    continue
                elif segments == 0:
                    msg = 'Reporter found 0 segments.'
                    df.loc[outDfRow, scoring_metrics + ['reporter_url']] = \
                        [-1] * 6 + [reportUrl]
                    continue
                segScore, distScore, undermatchScore, undermatchLenScore, \
                    overmatchScore, overmatchLenScore = get_match_scores(
                        segments, dfEdges, gpsMatchEdges)
                edgeSpeedScore, pctTooFastEdges, pctTooSlowEdges, \
                    segSpeedScore, pctTooFastSegs, pctTooSlowSegs, \
                    segMatchSpeedScore, segMissSpeedScore, \
                    segSpeedDf = get_speed_scores(
                        gpsMatchEdges, dfEdges, segments, sampleRate)
                if len(segSpeedDf) < 1:
                    continue
                segSpeedDf.loc[:, 'route_name'] = routeName
                segSpeedDf.loc[:, 'sample_rate'] = sampleRate
                segSpeedDf.loc[:, 'noise'] = noise
                speedDf = pd.concat((speedDf, segSpeedDf), ignore_index=True)

                df.loc[outDfRow, scoring_metrics + ['reporter_url']] = [
                    segScore, distScore, undermatchScore, undermatchLenScore,
                    overmatchScore, overmatchLenScore, edgeSpeedScore,
                    pctTooFastEdges, pctTooSlowEdges, segSpeedScore,
                    pctTooFastSegs, pctTooSlowSegs, segMatchSpeedScore,
                    segMissSpeedScore, reportUrl]
                df.loc[outDfRow, 'avg_density'] = avgDensity
                df['segments'] = df['segments'].astype(float)
                df['overmatches'] = df['overmatches'].astype(float)
                df['undermatches'] = df['undermatches'].astype(float)
                df['distance traveled'] = df['distance traveled'].astype(float)
                df['overmatch distance'] = df[
                    'overmatch distance'].astype(float)
                df['undermatch distance'] = df[
                    'undermatch distance'].astype(float)
                df['avg_density'] = df['avg_density'].astype(float)
                df['noise'] = df['noise'].astype(float)
                df['sample_rate'] = df['sample_rate'].astype(float)
                df['score_density'] = df['segments'] * df['avg_density']

                if saveResults:
                    with open(
                        '../data/trace_{0}_to_{1}_w_{2}'
                        '_m_noise_at_{3}_Hz.geojson'.format(
                            stName, endName, str(noise), str(Hz)), 'w+') as fp:
                                json.dump(geojson, fp)

    return df, speedDf


def plot_segment_match_boxplots(df, sampleRates, saveFig=True):
    for rate in sampleRates:
        Hz = round(1 / rate, 3)
        fig, ax = plt.subplots(figsize=(12, 8))
        df[(df['sample_rate'] == rate) & (df['distance traveled'] >= 0)]. \
            boxplot(column='distance traveled', by='noise', ax=ax, grid=True)
        ax.set_ylim(0, 3)
        ax.set_xlabel('Noise (m)', fontsize=15)
        ax.set_ylabel('Match rate', fontsize=15)
        ax.set_title('Sample Rate: {0} Hz'.format(Hz), fontsize=20)
        fig.suptitle('')
        if saveFig:
            fig.savefig('./../data/score_vs_noise_{0}_Hz.png'.format(Hz))


def plot_distance_metrics(df, sampleRates, saveFig=True):

    norm = plt.Normalize()
    cmap = plt.get_cmap('RdYlBu_r')
    colors = cmap(norm(sampleRates))
    distance_metrics = [
        'segments', 'distance traveled', 'undermatches',
        'undermatch distance', 'overmatches', 'overmatch distance']
    metricArr = np.asarray(distance_metrics).reshape((3, 2))
    fig, axarr = plt.subplots(3, 2, sharex=True, figsize=(16, 16))
    for i, row in enumerate(axarr):
        for j, col in enumerate(row):
            metric = metricArr[i, j]
            data = df[['noise', metric, 'sample_rate']].groupby(
                ['sample_rate', 'noise']).agg('median').reset_index()
            for k, rate in enumerate(sampleRates):
                axarr[i, j].plot(
                    data.loc[data['sample_rate'] == rate, 'noise'],
                    data.loc[data['sample_rate'] == rate, metric],
                    label=str(round(1 / rate, 3)) + ' Hz', alpha=0.7,
                    color=colors[k])
            axarr[i, j].legend(title='Sample Rate')
            axarr[i, j].set_title(metric)

    ax = fig.add_subplot(111, frameon=False)
    plt.tick_params(
        labelcolor='none', top='off', bottom='off', left='off', right='off')
    ax.set_xlabel('Noise (m)', fontsize=15)
    ax.set_ylabel('Match Error Rate', fontsize=15)
    if saveFig:
        fig.savefig('match_errors_by_sample_rate.png')


def get_optimal_speed_error_threshold(speedDf, plot=True, saveFig=True):
    fig, ax = plt.subplots(figsize=(12, 8))

    # matched_n, bins, patches = ax.hist(
    #     speedDf.loc[speedDf['matched'] == True, 'pct_error'], bins=100,
    #     range=(-2, 11), label="matched segments", cumulative=True,
    #     normed=True, histtype='step', linewidth=1.5, color='red')
    # missed_n, bins, patches = ax.hist(
    #     speedDf.loc[speedDf['matched'] == False, 'pct_error'], bins=100,
    #     range=(-2, 11), label="unmatched segments", cumulative=True,
    #     normed=True, histtype='step', linewidth=1.5, color='blue')

    # bincenters = 0.5 * (bins[1:] + bins[:-1])
    # freqDiff = matched_n - missed_n
    # errorAtMaxDiff = bincenters[np.argmax(freqDiff)]
    matchedSorted = speedDf.loc[
        speedDf['matched'], 'pct_error'].sort_values()
    matchDensity, matchBinEdges = np.histogram(
        matchedSorted, normed=True, bins=300, density=True)
    matchUnityDensity = matchDensity / matchDensity.sum()
    matchCdf = np.cumsum(matchUnityDensity)

    missedSorted = speedDf.loc[
        speedDf['matched'] == False, 'pct_error'].sort_values()
    missDensity, missBinEdges = np.histogram(
        missedSorted, normed=True, bins=300, density=True)
    missUnityDensity = missDensity / missDensity.sum()
    missCdf = np.cumsum(missUnityDensity)
    interpMissCdf = np.interp(
        matchBinEdges[:-1], missBinEdges[:-1], missCdf)

    alignedDiff = matchCdf - interpMissCdf
    maxDiffIdx = np.argmax(alignedDiff)
    errorAtMaxDiff = matchBinEdges[:-1][maxDiffIdx]

    truePositiveRate = matchCdf[maxDiffIdx]
    truePostiveRateStr = np.round(truePositiveRate * 100, 1)
    falsePositiveRate = interpMissCdf[maxDiffIdx]
    falsePostiveRateStr = np.round(falsePositiveRate * 100, 1)
    maxDiffPctStr = np.round((errorAtMaxDiff * 100), 1)

    ax.plot(matchBinEdges[:-1], matchCdf, color='b', label='matched segments')
    ax.plot(matchBinEdges[:-1], interpMissCdf, color='r',
            label='unmatched segments')
    ax.set_ylim(-0.01, 1.01)
    ax.axvline(errorAtMaxDiff, linewidth=0.5, color='r')
    ax.annotate(
        'True Positive Rate: {0}%'.format(truePostiveRateStr),
        xy=(errorAtMaxDiff, truePositiveRate), xytext=(0.6, 0.75),
        textcoords='figure fraction', arrowprops=dict(
            width=0.05, facecolor='black'))
    ax.annotate(
        'False Positive Rate: {0}%'.format(falsePostiveRateStr),
        xy=(errorAtMaxDiff, falsePositiveRate), xytext=(0.6, 0.5),
        textcoords='figure fraction', arrowprops=dict(
            width=0.05, facecolor='black'))
    ax.annotate(
        'Optimal Error Threshold: {0}%'.format(maxDiffPctStr),
        xy=(errorAtMaxDiff, 0.1), xytext=(0.6, 0.25),
        textcoords='figure fraction', arrowprops=dict(
            width=0.05, facecolor='black'))
    ax2 = ax.twinx()
    ax2.plot(matchBinEdges[:-1], alignedDiff, linewidth=1, color='k',
             linestyle='--', label='Frequency Difference')
    ax2.legend(loc='lower right')
    ax2.set_ylabel('Difference in Cumulative Frequency', fontsize=15)
    ax.legend(loc='upper right')
    ax.set_xlim(-1, 10)
    ax.set_xlabel("% Error: Segment Speed", fontsize=15)
    ax.set_ylabel("Cumulative Frequency", fontsize=15)
    if not plot:
        plt.close()
    else:
        plt.show()
    if saveFig:
        fig.savefig('speed_error_cdfs.png')

    return errorAtMaxDiff


def plot_accuracy_heatmap(speedDf, thresholds, sampleRates,
                          noiseLevels, saveFig=True):
    accMat = np.ones((len(sampleRates), len(noiseLevels)))
    for i, sampleRate in enumerate(sampleRates):
        if len(thresholds) == len(sampleRates):
            threshold = thresholds[i]
        else:
            threshold = thresholds[0]
        for j, noiseLevel in enumerate(noiseLevels):
            df = speedDf.loc[
                (speedDf['sample_rate'] == sampleRate) &
                (speedDf['noise'] == noiseLevel)]

            numTruePos = len(df.loc[
                (speedDf['matched']) &
                (speedDf['pct_error'] <= threshold)])

            numTrueNeg = len(df.loc[
                (speedDf['matched'] == False) &
                (speedDf['pct_error'] > threshold)])

            acc = (numTruePos + numTrueNeg) / len(df)
            accMat[i, j] = acc

    fig, ax = plt.subplots(figsize=(12, 12))
    im = ax.imshow(accMat, interpolation='none', extent=[
        min(noiseLevels), max(noiseLevels), max(sampleRates),
        min(sampleRates)])
    plt.colorbar(im, fraction=0.02)
    ax.set_xlabel("noise", fontsize=15)
    ax.set_ylabel("sample rate", fontsize=15)
    ax.set_yticks(
        np.arange(min(sampleRates), max(sampleRates), len(sampleRates)))
    ax.set_yticklabels([''] + map(str, (map(int, sampleRates))))
    ax.set_title("Accuracy at Optimal Error Threshold", fontsize=15)
    plt.show()
    if saveFig:
        fig.savefig('map_matching_acc_at_threshold.png')
    return accMat


def plot_change_in_acc(oneSizeFitsAllAcc, rateSpecificAcc, sampleRates,
                       noiseLevels):
    fig, ax = plt.subplots(figsize=(12, 12))
    ax.set_xlabel("noise", fontsize=15)
    ax.set_ylabel("sample rate", fontsize=15)
    ax.set_yticks(
        np.arange(min(sampleRates), max(sampleRates), len(sampleRates)))
    ax.set_yticklabels([''] + map(str, (map(int, sampleRates))))
    accDiff = rateSpecificAcc - oneSizeFitsAllAcc
    limit = np.max(np.abs(accDiff))
    im = ax.imshow(
        accDiff, cmap='RdYlGn', vmin=-limit, vmax=limit,
        extent=[min(noiseLevels), max(noiseLevels),
                max(sampleRates), min(sampleRates)])
    ax.set_title(
        "Change in Accuracy Using Rate-Specific Thresholds", fontsize=15)
    plt.colorbar(im, fraction=0.02)


def convert_coords_to_meters(coords, localEpsg, inputOrder='lonlat'):
    if inputOrder == 'latlon':
        indices = [1, 0]
    elif inputOrder == 'lonlat':
        indices = [0, 1]
    else:
        print('"inputOrder" param cannot be processed')
    inProj = Proj(init='epsg:4326')
    outProj = Proj(init='epsg:{0}'.format(localEpsg))
    projCoords = [
        transform(inProj, outProj, coord[indices[0]], coord[indices[1]])
        for coord in coords]
    return projCoords


def convert_coords_to_lat_lon(coords, localEpsg, inputOrder='xy'):
    if inputOrder == 'yx':
        indices = [1, 0]
    elif inputOrder == 'xy':
        indices = [0, 1]
    else:
        print('"inputOrder" param cannot be processed')
    inProj = Proj(init='epsg:{0}'.format(localEpsg))
    outProj = Proj(init='epsg:4326')
    projCoords = [
        transform(inProj, outProj, coord[indices[0]], coord[indices[1]])
        for coord in coords]
    return projCoords


def decode(encoded):
    inv = 1.0 / 1e6
    decoded = []
    previous = [0, 0]
    i = 0
    while i < len(encoded):
        ll = [0, 0]
        for j in [0, 1]:
            shift = 0
            byte = 0x20
            while byte >= 0x20:
                byte = ord(encoded[i]) - 63
                i += 1
                ll[j] |= (byte & 0x1f) << shift
                shift += 5
            ll[j] = previous[j] + \
                (~(ll[j] >> 1) if ll[j] & 1 else (ll[j] >> 1))
            previous[j] = ll[j]
        decoded.append(
            [float('%.6f' % (ll[1] * inv)), float('%.6f' % (ll[0] * inv))])
    return decoded


def get_coords_per_second(shapeCoords, edges, localEpsg):
    mProj = Proj(init='epsg:{0}'.format(localEpsg))
    llProj = Proj(init='epsg:4326')
    coords = shapeCoords
    projCoords = convert_coords_to_meters(coords, localEpsg=localEpsg)
    for i, edge in enumerate(edges):
        subSegmentCoords = []
        if i == 0:
            subSegmentCoords.append(coords[edge['begin_shape_index']])
        dist = edge['length']
        distMeters = dist * 1e3
        speed = edge['speed']
        mPerSec = speed * 1e3 / 3600.0
        beginShapeIndex = edge['begin_shape_index']
        endShapeIndex = edge['end_shape_index']
        if (beginShapeIndex >= len(coords) - 1) | \
           (endShapeIndex >= len(coords)):
            continue
        line = LineString(projCoords[beginShapeIndex:endShapeIndex + 1])
        seconds = 0
        while mPerSec * seconds < distMeters:
            seconds += 1
            newPoint = line.interpolate(mPerSec * seconds)
            newLon, newLat = transform(mProj, llProj, newPoint.x, newPoint.y)
            subSegmentCoords.append([newLon, newLat])
        if i == len(edges) - 1:
            subSegmentCoords.append(coords[edge['end_shape_index']])
        edge['oneSecCoords'] = subSegmentCoords
        edge['numOneSecCoords'] = len(subSegmentCoords)
    return edges


def synthesize_gps(dfEdges, shapeCoords, localEpsg, distribution="normal",
                   noise=0, sampleRate=1, uuid="999999", shapeMatch="map_snap",
                   mode="auto", turnPenaltyFactor=0, breakageDist=2000, beta=3,
                   sigmaZ=4.07, searchRadius=50):

    accuracy = round(min(100, norm.ppf(0.95, loc=0, scale=max(1, noise))), 2)
    mProj = Proj(init='epsg:{0}'.format(localEpsg))
    llProj = Proj(init='epsg:4326')
    jsonDict = {
        "uuid": uuid, "trace": [], "shape_match": shapeMatch,
        "match_options": {
            "mode": mode,
            "turn_penalty_factor": turnPenaltyFactor,
            "breakage_distance": breakageDist,
            "beta": beta,
            "sigma_z": sigmaZ,
            "search_radius": searchRadius,
            "gps_accuracy": accuracy}}
    trueRouteCoords = []
    resampledCoords = []
    gpsRouteCoords = []
    displacementLines = []
    lonAdjs = []
    latAdjs = []
    noiseLookback = int(np.ceil(30 / (sampleRate + 2)))
    sttm = int(t.time()) - 86400   # yesterday
    seconds = 0
    shapeIndexCounter = 0
    for i, edge in dfEdges.iterrows():
        if i == 0:
            trueCoords = shapeCoords[edge['begin_shape_index']]
            trueRouteCoords.append(trueCoords)
        trueCoords = shapeCoords[edge['end_shape_index']]
        trueRouteCoords.append(trueCoords)
        edgeShapeIndices = []
        for j, coordPair in enumerate(edge['oneSecCoords']):
            if (not seconds % sampleRate) | (
                (i + 1 == len(dfEdges)) &
                (j + 1 == len(edge['oneSecCoords']))
            ):
                lon, lat = coordPair
                resampledCoords.append([lon, lat])
                if noise > 0:
                    projLon, projLat = transform(llProj, mProj, lon, lat)
                    while True:
                        lonAdj = np.random.normal(scale=noise)
                        latAdj = np.random.normal(scale=noise)
                        if shapeIndexCounter == 0:
                            noiseQuad = [np.sign(lonAdj), np.sign(latAdj)]
                            break
                        elif [np.sign(lonAdj), np.sign(latAdj)] == noiseQuad:
                            break
                    lonAdjs.append(lonAdj)
                    latAdjs.append(latAdj)
                    newProjLon = projLon + np.mean(lonAdjs[-noiseLookback:])
                    newProjLat = projLat + np.mean(latAdjs[-noiseLookback:])
                    projLon, projLat = newProjLon, newProjLat
                    lon, lat = transform(mProj, llProj, projLon, projLat)
                time = sttm + seconds
                lat = round(lat, 6)
                lon = round(lon, 6)
                jsonDict["trace"].append({
                    "lat": lat, "lon": lon, "time": time})
                gpsRouteCoords.append([lon, lat])
                displacementLines.append([coordPair, [lon, lat]])
                edgeShapeIndices.append(shapeIndexCounter)
                shapeIndexCounter += 1
            seconds += 1
        if len(edgeShapeIndices) > 0:
            dfEdges.loc[
                i, 'begin_resampled_shape_index'] = min(edgeShapeIndices)
            dfEdges.loc[
                i, 'end_resampled_shape_index'] = max(edgeShapeIndices)

    gpsShape = [{"lat": d["lat"], "lon": d["lon"]} for d in jsonDict['trace']]
    gpsMatchEdges, gpsMatchCoords, _ = get_trace_attrs(
        gpsShape, encoded=False, gpsAccuracy=accuracy, mode=mode,
        turnPenaltyFactor=turnPenaltyFactor, breakageDist=breakageDist,
        beta=beta, sigmaZ=sigmaZ, searchRadius=searchRadius)

    geojson = FeatureCollection([
        Feature(geometry=LineString(
            trueRouteCoords), properties={"style": {
                "color": "#ff0000",
                "weight": "3px"},
                "name": "true_route_coords"}),
        Feature(geometry=MultiPoint(
            resampledCoords), properties={"style": {
                "color": "#ff0000",
                "weight": "3px"},
                "name": "resampled_coords"}),
        Feature(geometry=MultiPoint(
            gpsRouteCoords), properties={"style": {
                "color": "#0000ff",
                "weight": "3px"},
                "name": "gps_coords"}),
        Feature(geometry=MultiLineString(
            displacementLines), properties={"style": {
                "color": "#000000",
                "weight": "1px",
                "name": "displacement_lines"}}),
        Feature(geometry=LineString(
            gpsMatchCoords), properties={"style": {
                "fillcolor": "#0000ff",
                "weight": "3px",
                "name": "matched_gps_route"}})])

    return dfEdges, jsonDict, geojson, gpsMatchEdges


def get_route_shape(routeCoords):

    stLat = routeCoords[0].values()[0]["lat"]
    stLon = routeCoords[0].values()[0]["lon"]
    endLat = routeCoords[1].values()[0]["lat"]
    endLon = routeCoords[1].values()[0]["lon"]
    jsonDict = {"locations": [{
        "lat": stLat, "lon": stLon, "type": "break"},
        {
        "lat": endLat, "lon": endLon, "type": "break"}],
        "costing": "auto",
        "id": "my_work_route"}
    payload = {"json": json.dumps(jsonDict, separators=(',', ':'))}
    baseUrl = 'http://valhalla:8002/route'
    route = requests.get(baseUrl, params=payload)
    shape = route.json()['trip']['legs'][0]['shape']

    if route.status_code == 200:
        return shape, route.url
    else:
        return None, 'No shape returned.'


def get_trace_attrs(shape, encoded=True, shapeMatch='map_snap',
                    gpsAccuracy=5, mode="auto", turnPenaltyFactor=0,
                    breakageDist=2000, beta=3, sigmaZ=4.07, searchRadius=50):
    if encoded:
        shapeParam = 'encoded_polyline'
    else:
        shapeParam = 'shape'

    jsonDict = {
        shapeParam: shape,
        "costing": "auto",
        "shape_match": shapeMatch,
        "trace_options": {
            "gps_accuracy": gpsAccuracy,
            "turn_penalty_factor": turnPenaltyFactor,
            "breakage_distance": breakageDist,
            "beta": beta,
            "sigma_z": sigmaZ,
            "search_radius": searchRadius
        }
    }
    payload = {"json": json.dumps(jsonDict, separators=(',', ':'))}
    baseUrl = 'http://valhalla:8002/trace_attributes?'
    matched = requests.get(baseUrl, params=payload)
    edges = matched.json()['edges']
    matchedPts = decode(matched.json()['shape'])
    return edges, matchedPts, matched.url


def format_edge_df(edges):

    dfEdges = pd.DataFrame(edges)
    dfEdges = dfEdges[[
        'id', 'begin_shape_index', 'end_shape_index', 'length',
        'speed', 'density', 'traffic_segments', 'oneSecCoords']]
    dfEdges['segment_id'] = dfEdges['traffic_segments'].apply(
        lambda x: str(x[0]['segment_id']) if type(x) is list else None)
    dfEdges['num_segments'] = dfEdges['traffic_segments'].apply(
        lambda x: len(x) if type(x) is list else 0)
    dfEdges['starts_segment'] = dfEdges['traffic_segments'].apply(
        lambda x: x[0]['starts_segment'] if type(x) is list else None)
    dfEdges['ends_segment'] = dfEdges['traffic_segments'].apply(
        lambda x: x[0]['starts_segment'] if type(x) is list else None)
    dfEdges['begin_percent'] = dfEdges['traffic_segments'].apply(
        lambda x: x[0]['begin_percent'] if type(x) is list else None)
    dfEdges['end_percent'] = dfEdges['traffic_segments'].apply(
        lambda x: x[0]['end_percent'] if type(x) is list else None)
    dfEdges.drop('traffic_segments', axis=1, inplace=True)
    dfEdges['begin_resampled_shape_index'] = None
    dfEdges['end_resampled_shape_index'] = None
    return dfEdges


def get_reporter_segments(gpsTrace):

    baseUrl = 'http://reporter:8003/report'
    payload = {"json": json.dumps(gpsTrace, separators=(',', ':'))}
    report = requests.get(baseUrl, params=payload)
    # report = requests.post(baseUrl, json=gpsTrace)
    if report.status_code == 200:
        segments = report.json()['segment_matcher']['segments']
    else:
        return None, report.reason
    if len(segments) > 0:
        return segments, report.url
    else:
        return 0, report.url


def get_match_scores(segments, dfEdges, gpsMatchEdges):

    segDf = pd.DataFrame(segments, columns=[
        'begin_shape_index', 'end_shape_index', 'end_time', 'internal',
        'segment_id', 'length', 'start_time'])
    segDf = segDf[~pd.isnull(segDf['segment_id'])]
    segDf.loc[:, 'segment_id'] = segDf['segment_id'].astype(int).astype(str)
    segMatches = segDf['segment_id'].isin(dfEdges['segment_id'])
    edgeMatches = dfEdges['segment_id'].isin(segDf['segment_id'])
    segScore = 1 - ((np.sum(segMatches) + np.sum(edgeMatches)) /
                    (len(segMatches) + len(edgeMatches)))

    distTraveled = np.sum(dfEdges['length'])
    dfGpsEdges = pd.DataFrame(gpsMatchEdges)
    overmatchMask = ~dfGpsEdges['id'].isin(dfEdges['id'])
    undermatchMask = ~dfEdges['id'].isin(dfGpsEdges['id'])

    overmatchScore = np.sum(overmatchMask) / len(dfGpsEdges)
    overmatchLen = np.sum(
        dfGpsEdges.loc[overmatchMask, 'length'])
    overmatchLenScore = overmatchLen / distTraveled

    undermatchScore = np.sum(undermatchMask) / len(dfEdges)
    undermatchLen = np.sum(
        dfEdges.loc[undermatchMask, 'length'])
    undermatchLenScore = undermatchLen / distTraveled
    lenScore = (overmatchLen + undermatchLen) / distTraveled
    return segScore, lenScore, undermatchScore, undermatchLenScore, \
        overmatchScore, overmatchLenScore


def get_speed_scores(gpsMatchEdges, dfEdges, segments, sampleRate):

    gpsEdgeSpeeds = pd.DataFrame([(
        edge['id'], edge['begin_shape_index'], edge['end_shape_index'],
        edge['length'])for edge in gpsMatchEdges],
        columns=['id', 'begin_shape_index', 'end_shape_index', 'length'])
    gpsEdgeSpeeds['speed'] = gpsEdgeSpeeds['length'] / (
        (gpsEdgeSpeeds['end_shape_index'] -
            gpsEdgeSpeeds['begin_shape_index']) * (sampleRate / 3600))
    gpsEdgeSpeeds = gpsEdgeSpeeds[['id', 'length', 'speed']]
    gpsEdgeSpeeds['matched'] = gpsEdgeSpeeds['id'].isin(
        dfEdges['id'])

    osmEdgeSpeeds = dfEdges[['id', 'length', 'speed']]

    edgeSpeeds = pd.merge(
        osmEdgeSpeeds, gpsEdgeSpeeds, on="id", how="inner",
        suffixes=("_osm", "_gps"))
    edgeSpeeds['pct_error'] = (
        edgeSpeeds['speed_gps'] -
        edgeSpeeds['speed_osm']) / edgeSpeeds['speed_osm']
    edgeSpeedScore = edgeSpeeds['pct_error'].median()
    if len(edgeSpeeds) > 0:
        pctTooFastEdges = np.sum(
            edgeSpeeds['pct_error'] > 1.5) / len(edgeSpeeds)
        pctTooSlowEdges = np.sum(
            edgeSpeeds['pct_error'] < -0.5) / len(edgeSpeeds)
    else:
        pctTooFastEdges, pctTooSlowEdges = None, None

    segDf = pd.DataFrame(segments)
    segDf = segDf[(
        ~pd.isnull(segDf['segment_id'])) & (
        segDf['end_time'] != -1) & (
        segDf['start_time'] != -1)]
    segDf['speed'] = segDf['length'] / (
        segDf['end_time'] - segDf['start_time']) * 3.6
    segDf['segment_id'] = segDf['segment_id'].astype(int).astype(str)

    gpsEdgeDf = pd.DataFrame(gpsMatchEdges)
    gpsEdgeDf['segment_id'] = gpsEdgeDf['traffic_segments'].apply(
        lambda x: str(x[0]['segment_id']) if type(x) is list else None)
    osmSegSpeeds = gpsEdgeDf[['segment_id', 'speed']].groupby(
        'segment_id').agg('median').reset_index()
    gpsSegSpeeds = segDf[['segment_id', 'speed']].groupby(
        'segment_id').agg('median').reset_index()
    gpsSegSpeeds['matched'] = gpsSegSpeeds['segment_id'].isin(
        dfEdges['segment_id'])

    segSpeeds = pd.merge(
        osmSegSpeeds, gpsSegSpeeds, on='segment_id', how='inner',
        suffixes=('_osm', '_gps'))
    segSpeeds['pct_error'] = (
        segSpeeds['speed_gps'] -
        segSpeeds['speed_osm']) / segSpeeds['speed_osm']
    segSpeedScore = segSpeeds['pct_error'].median()

    if len(segSpeeds) > 0:
        pctTooFastSegs = np.sum(
            segSpeeds['pct_error'] > 1.5) / len(segSpeeds)
        pctTooSlowSegs = np.sum(
            segSpeeds['pct_error'] < -0.5) / len(segSpeeds)
        matchSpeedDf = segSpeeds[[
            'matched', 'pct_error']].groupby(
                'matched').agg('median').reset_index()
        try:
            segMatchSpeedScore = matchSpeedDf.loc[
                matchSpeedDf['matched'] == True, 'pct_error'].values[0]
        except IndexError:
            segMatchSpeedScore = None
        try:
            segMissSpeedScore = matchSpeedDf.loc[
                matchSpeedDf['matched'] == False, 'pct_error'].values[0]
        except IndexError:
            segMissSpeedScore = None
    else:
        pctTooFastSegs, pctTooSlowSegs, segMatchSpeedScore, \
            segMissSpeedScore = None, None, None, segSpeedScore

    return edgeSpeedScore, pctTooFastEdges, pctTooSlowEdges, segSpeedScore, \
        pctTooFastSegs, pctTooSlowSegs, segMatchSpeedScore, \
        segMissSpeedScore, segSpeeds[['segment_id', 'pct_error', 'matched']]


def get_POI_routes_by_length(locString, minRouteLength, maxRouteLength,
                             numResults, apiKey):

    baseUrl = 'https://maps.googleapis.com/maps/api/place' + \
        '/textsearch/json?query={0}&radius={1}&key={2}'
    baseUrl = baseUrl.format("{0} point of interest".format(
        locString), 25000, apiKey)
    tokenStr = ''
    goodRoutes = []
    sttm = t.time()
    while (len(goodRoutes) < numResults) & (t.time() - sttm < 300):
        r = requests.get(baseUrl + tokenStr)
        POIs = [{x['name']: {
            "lat": x['geometry']['location']['lat'],
            "lon": x['geometry']['location']['lng']}}
            for x in r.json()['results']]
        routeList = list(itertools.combinations(POIs, 2))
        shuffle(routeList)
        for route in routeList:
            stLat = route[0].values()[0]["lat"]
            stLon = route[0].values()[0]["lon"]
            endLat = route[1].values()[0]["lat"]
            endLon = route[1].values()[0]["lon"]
            jsonDict = {"locations": [{
                "lat": stLat, "lon": stLon, "type": "break"},
                {
                "lat": endLat, "lon": endLon, "type": "break"}],
                "costing": "auto",
                "id": "my_work_route"}
            payload = {"json": json.dumps(jsonDict, separators=(',', ':'))}
            baseUrlValhalla = 'http://valhalla:8002/route'
            routeCheck = requests.get(baseUrlValhalla, params=payload)
            if routeCheck.status_code != 200:
                continue
            length = routeCheck.json()['trip']['summary']['length']
            if minRouteLength < length < maxRouteLength:
                goodRoutes.append(route)
        try:
            nextPageToken = r.json()['next_page_token']
            tokenStr = "&pagetoken={0}".format(nextPageToken)
        except KeyError:
            break
    shuffle(goodRoutes)
    numResults = min(len(goodRoutes), numResults)
    goodRoutes = goodRoutes[:numResults]
    return goodRoutes


def get_routes_by_length(cityStr, minRouteLength, maxRouteLength,
                         numResults, apiKey):

    mapzenKey = apiKey

    baseUrl = 'https://search.mapzen.com/v1/search?'
    cityQuery = 'sources={0}&text={1}&api_key={2}&layer={3}&size=1'.format(
        'whosonfirst', cityStr, mapzenKey, 'locality')
    city = requests.get(baseUrl + cityQuery)
    cityID = city.json()['features'][0]['properties']['source_id']
    goodRoutes = []
    baseUrlCity = 'https://whosonfirst-api.mapzen.com?' + \
        'api_key={0}&'.format(mapzenKey)

    venueQuery = 'method={0}&id={1}&placetype={2}'.format(
        'whosonfirst.places.getDescendants', cityID, 'venue') + \
        '&page=1&per_page=2000'
    venues = requests.get(baseUrlCity + venueQuery)
    venueIDs = [x['wof:id'] for x in venues.json()['places']]
    shuffle(venueIDs)
    venueListBreakPoints = range(0, len(venueIDs), 20)
    venueListIter = 0
    sttm = t.time()

    while (len(goodRoutes) < numResults) & (t.time() - sttm < 300):
        venueChunkIdx = venueListBreakPoints[venueListIter]
        POIs = []
        baseUrlVenues = 'https://whosonfirst-api.mapzen.com?' + \
            'api_key={0}&page=1&per_page=1&'.format(mapzenKey) + \
            'extras=geom:latitude,geom:longitude'
        for venueID in venueIDs[venueChunkIdx:venueChunkIdx + 20]:
            geoQuery = '&method={0}&id={1}&placetype={2}'.format(
                'whosonfirst.places.getInfo', venueID, 'venue')
            info = requests.get(baseUrlVenues + geoQuery).json()['place']
            POIs.append({info['wof:name']: {
                "lat": info['geom:latitude'],
                "lon": info['geom:longitude']}})
        routeList = list(itertools.combinations(POIs, 2))
        for route in routeList:
            stLat = route[0].values()[0]["lat"]
            stLon = route[0].values()[0]["lon"]
            endLat = route[1].values()[0]["lat"]
            endLon = route[1].values()[0]["lon"]
            jsonDict = {"locations": [{
                "lat": stLat, "lon": stLon, "type": "break"},
                {
                "lat": endLat, "lon": endLon, "type": "break"}],
                "costing": "auto",
                "id": "my_work_route"}
            payload = {"json": json.dumps(jsonDict, separators=(',', ':'))}
            baseUrlValhalla = 'http://valhalla:8002/route'
            routeCheck = requests.get(baseUrlValhalla, params=payload)
            length = routeCheck.json()['trip']['summary']['length']
            if minRouteLength < length < maxRouteLength:
                goodRoutes.append(route)
        venueListIter += 1

    shuffle(goodRoutes)
    goodRoutes = goodRoutes[:numResults]
    return goodRoutes


def generate_route_map(pathToGeojson, zoomLevel=11):

    with open(pathToGeojson, "r") as f:
        data = json.load(f)
    ctrLon, ctrLat = np.mean(
        np.array(data['features'][0]['geometry']['coordinates']), axis=0)
    url = "http://stamen-tiles-{s}.a.ssl.fastly.net/toner-lite/{z}/{x}/{y}.png"
    provider = TileLayer(url=url, opacity=1)
    center = [ctrLat, ctrLon]
    m = Map(default_tiles=provider, center=center, zoom=zoomLevel)
    m.layout = Layout(width='100%', height='800px')
    trueRouteCoords, resampledCoords, gpsRouteCoords, \
        displacementLines, gpsMatchCoords = data['features']
    g = GeoJSON(data=FeatureCollection(
        [trueRouteCoords, gpsMatchCoords]))
    m.add_layer(g)
    for coords in resampledCoords['geometry']['coordinates']:
        cm = Circle(
            location=coords[::-1], radius=10, weight=1, color='#ff0000',
            opacity=1.0, fill_opacity=0.4, fill_color='#ff0000')
        m.add_layer(cm)
    for coords in gpsRouteCoords['geometry']['coordinates']:
        cm = Circle(
            location=coords[::-1], radius=10, weight=1, color='#0000ff',
            opacity=1.0, fill_opacity=0.4, fill_color='#0000ff')
        m.add_layer(cm)
    g = GeoJSON(data=displacementLines)
    m.add_layer(g)
    return m


def getLineFromPoints(point1, point2):

    x1, y1 = point1
    x2, y2 = point2
    m = (y2 - y1) / (x2 - x1)
    b = y1 - (m * x1)
    return m, b


def getPerpLineThruEndpt(slope, endpoint):

    m = -1 / slope
    x, y = endpoint
    b = y - (m * x)
    return m, b


def getBoundaryLineCoords(slope, intercept, midpoint, noise, localEpsg='2768'):

    midx, midy = midpoint
    tmpLeftX = midx - (noise * 2)
    tmpLeftY = slope * tmpLeftX + intercept
    leftBisect = LineString([[midx, midy], [tmpLeftX, tmpLeftY]])
    leftEndPt = leftBisect.interpolate(noise * 2)
    tmpRightX = midx + (noise * 2)
    tmpRightY = slope * tmpRightX + intercept
    rightBisect = LineString([[midx, midy], [tmpRightX, tmpRightY]])
    rightEndPt = rightBisect.interpolate(noise * 2)
    boundaryLine = LineString([leftEndPt, midpoint, rightEndPt])
    boundaryLineCoords = [
        [endpt.xy[0][0], endpt.xy[1][0]] for endpt in boundaryLine.boundary]
    mProj = Proj(init='epsg:{0}'.format(localEpsg))
    llProj = Proj(init='epsg:4326')
    boundaryLineCoords = [
        transform(mProj, llProj, x[0], x[1]) for x in boundaryLineCoords]
    return boundaryLineCoords


def checkForBackTrack(lastSegCoords, newPoint, noise):
    lastSegSlope, lastSegIntercept = getLineFromPoints(
        lastSegCoords[0], lastSegCoords[1])
    perpLineSlope, perpLineIntercept = getPerpLineThruEndpt(
        lastSegSlope, lastSegCoords[1])
    bl = getBoundaryLineCoords(
        perpLineSlope, perpLineIntercept, lastSegCoords[1], noise)
    firstPtPos = np.sign(
        perpLineSlope * lastSegCoords[0][0] +
        perpLineIntercept - lastSegCoords[0][1])
    newPtPos = np.sign(
        perpLineSlope * newPoint[0] + perpLineIntercept - newPoint[1])
    if firstPtPos == newPtPos:
        return True, bl
    else:
        return False, bl
