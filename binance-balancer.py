'''
Binance Balancer
2020 - Sandy Bay

Re-balances every hour based on manually fixed allocations
Defaults to limit orders which are cancelled if unfilled and recalculated for the new rebalance

'''
import math
import time
import pandas as pd
import numpy as np
from binance.client import Client
from apscheduler.schedulers.blocking import BlockingScheduler
from csv import writer
from datetime import datetime
import pprint


# set keys
api_key = ''
api_secret = ''

# set weights
# look for 6 to 12 month value
# hedge fiat (usd,rub,try,eur)
# focus on trusted cryptos with the following priority
# security
# value
# usage
# fees
# privacy
# speed

lastweights = {
    "BAT":0.005,
    "ADA":0.005, 
    "EOS":0.005, 
    "NEO":0.005, 
    "XLM":0.02,
    "XRP":0.02,
    "DASH":0.01,
    "ETC":0.01,
    "BNB":0.03,
    "NANO":0.03,
    "ZEC":0.03, 
    "XMR":0.04,
    "LTC":0.04, 
    "BTC": 0.50,  
    "USDT": 0.25 } 

# Timestamped bitcoin and usd portfolio value
csvBalance = 'binance_balance_log.csv'

# globals
prices = {} # asset prices in btc
prices['BTC'] = 1.0
BTCUSD = 0.0
balances = {}
balancesbtc = {}
totalbtc = 0
diffs = {}
steps = {}
ticks = {}
minQtys = {}

# connect
client = Client(api_key, api_secret)
# time offset binance bug fix
# servTime = client.get_server_time()
# time_offset  = servTime['serverTime'] - int(time.time() * 1000)

def sanityCheck():
    sane = False
    sumWeights = round(sum(lastweights.values()),4)
    if sumWeights == 1.0000:
        sane = True
    else:
        print("Incorrect weights. Sum ratios must equal 1.0. Currently ",sumWeights)
    return sane

def append_list_as_row(file_name, list_of_elem):
    # Open file in append mode
    with open(file_name, 'a+', newline='') as write_obj:
        # Create a writer object from csv module
        csv_writer = writer(write_obj)
        # Add contents of list as last row in the csv file
        csv_writer.writerow(list_of_elem)

def saveBalance():
    # Returns a datetime object containing the local date and time
    dateTimeObj = datetime.now()
    # List of row elements (Timestamp, Bitcoin balance, USD balance, Notes)
    row_contents = [str(dateTimeObj), str(totalbtc) , str(totalbtc * BTCUSD)]
    # Append a list as new line to an old csv file
    append_list_as_row(csvBalance, row_contents)


def getPrices():
    global prices, BTCUSD
    # get prices
    priceinfo = client.get_all_tickers()
    for price in priceinfo:
        sym = price['symbol']
        asset = sym[0:-3]
        quote = sym[-3:]
        p = float(price['price'])
        if sym == 'BTCUSDT':
            BTCUSD = p
            prices['USDT'] = 1 / p
        elif quote == 'BTC':
            if asset in lastweights:
                prices[asset] = p
    print('Prices (BTC)')
    pprint.pprint(prices)

def getBalance():
    global balances, balancesbtc, totalbtc 
    totalbtc = 0
    # get balance
    info = client.get_account()
    for balance in info['balances']:
        free = float( balance['free'] ) 
        locked =  float( balance['locked'] )
        asset = balance['asset']
        if asset in lastweights:
            bal = free + locked
            balances[ asset ] = bal
            balancesbtc[ asset ] = bal * prices[asset]
            totalbtc = totalbtc + bal * prices[asset]
    # print(balances)
    print("Balances (BTC)")
    pprint.pprint(balancesbtc)
    print("Total (BTC / USD)")
    print(totalbtc," BTC /  $ ",totalbtc*BTCUSD)

def getDiffs():
    global diffs
    # get difference
    for asset in lastweights:
        adjshare = totalbtc * lastweights[asset]
        currshare = balancesbtc[asset]
        diff = adjshare - currshare
        diffs [ asset ] = diff
    diffs = dict(sorted(diffs.items(), key=lambda x: x[1]))
    print('Adjustments (BTC)')
    pprint.pprint(diffs)

def cancelOrders():
    # cancel current orders
    print('Canceling open orders')
    orders = client.get_open_orders()
    for order in orders:
        sym = order['symbol']
        asset = sym[0:-3]
        if sym == 'BTCUSDT' or asset in lastweights:
            orderid = order['orderId']
            result = client.cancel_order(symbol=sym,orderId=orderid)
            # print(result)

def step_size_to_precision(ss):
    return ss.find('1') - 1

def format_value(val, step_size_str):
    precision = step_size_to_precision(step_size_str)
    if precision > 0:
        return "{:0.0{}f}".format(val, precision)
    return math.floor(int(val))

def getSteps():
    global steps, ticks, minQtys
    # step sizes
    info = client.get_exchange_info()
    for dat in info['symbols']:
        sym = dat['symbol']
        asset = dat['baseAsset']
        quote = dat['quoteAsset']
        filters = dat['filters']
        if quote == 'BTC' and asset in lastweights:
            for filt in filters:
                if filt['filterType'] == 'LOT_SIZE':
                    steps[asset] = filt['stepSize']                     
                elif filt['filterType'] == 'PRICE_FILTER':
                    ticks[asset] = filt['tickSize']
                elif filt['filterType'] == 'MIN_NOTIONAL':
                    minQtys[asset] = filt['minNotional']
        elif sym == 'BTCUSDT':
            for filt in filters:
                if filt['filterType'] == 'LOT_SIZE':
                    steps[sym] = filt['stepSize']
                elif filt['filterType'] == 'PRICE_FILTER':
                    ticks[sym] = filt['tickSize']
                elif filt['filterType'] == 'MIN_NOTIONAL':
                    minQtys['USDT'] = filt['minNotional']


def placeOrders():
    # all go through btc
    # this can be smart routed later
    global diffs
    getSteps()
    # set sell orders
    for asset in diffs:
        diff = diffs[asset]
        if asset != 'BTC':
            thresh = float(minQtys[asset])
            if  diff <  -0.0001 : # threshold $ 1
                if asset != 'BTC' and asset != 'USDT':
                    sym = asset + 'BTC'
                    amountf = 0-diff # amount in btc
                                          
                    amount = format_value ( amountf / prices[asset] , steps[asset] )
                    price = format_value ( prices [ asset ] + 0.003 * prices [ asset ], ticks[asset] )# adjust for fee
                    minNotion = float(amount) * float(price)
                    if minNotion > thresh:
                        diffs[asset] = diffs[asset] + amountf
                        diffs['BTC'] = diffs['BTC'] - amountf    
                        print('Setting sell order for {}, amount:{}, price:{}, thresh:{}'.format(asset,amount,price,thresh))
                        order = client.order_limit_sell(
                            symbol = sym,
                            quantity = amount,
                            price = price )
                    
                elif asset == 'USDT':
                    sym = 'BTCUSDT'
                    amount = 0-diff
                    if amount > ( thresh / BTCUSD ):
                        diffs[asset] = diffs[asset] + amount
                        diffs['BTC'] = diffs['BTC'] - amount
                        amount = format_value ( amount  , steps[sym] )
                        price = format_value ( BTCUSD - 0.003 * BTCUSD , ticks[sym])# adjust for fee
                        print('Setting buy order for {}, amount:{}, price:{}'.format(asset,amount,price))
                        order = client.order_limit_buy(
                            symbol = sym,
                            quantity = amount,
                            price = price )
                


    # set buy orders
    diffs = dict(sorted(diffs.items(), key=lambda x: x[1], reverse=True))

    for asset in diffs:
        diff = diffs[ asset ]
        if asset != 'BTC':
            thresh = float( minQtys[ asset ] )
            if  diff >  0.0001 : # threshold $ 1
                if asset != 'BTC' and asset != 'USDT':
                    sym = asset + 'BTC'
                    amountf = diff

                    amount = format_value ( amountf / prices[asset] , steps[asset] )
                    price = format_value ( prices [ asset ] - 0.003 * prices [ asset ] , ticks[asset] )# adjust for fee
                    minNotion = float(amount) * float(price)
                    if minNotion > thresh:
                        diffs[asset] = diffs[asset] - amountf
                        diffs['BTC'] = diffs['BTC'] + amountf 
                        print('Setting buy order for {}, amount:{}, price:{}, thresh:{}'.format(asset,amount,price,thresh))
                        order = client.order_limit_buy(
                            symbol = sym,
                            quantity = amount,
                            price = price )
                    
                elif asset == 'USDT':
                    sym = 'BTCUSDT'
                    amount = diff
                    if amount > ( thresh / BTCUSD ):
                        diffs[asset] = diffs[asset] - amount
                        diffs['BTC'] = diffs['BTC'] + amount
                        amount = format_value ( amount  , steps[sym] )
                        price = format_value ( BTCUSD + 0.003 * BTCUSD , ticks[sym])# adjust for fee
                        print('Setting sell order for {}, amount:{}, price:{}'.format(asset,amount,price))
                        order = client.order_limit_sell(
                            symbol = sym,
                            quantity = amount,
                            price = price )
                

    # print ( 'Final differences' )
    # pprint.pprint ( diffs )

def iteratey():
    sane = sanityCheck()
    if sane == True:
        getPrices()
        getBalance()
        getDiffs()
        cancelOrders()
        placeOrders()   
        saveBalance() 

iteratey()

scheduler = BlockingScheduler()
scheduler.add_job(iteratey, 'interval', minutes=20)
scheduler.start()
