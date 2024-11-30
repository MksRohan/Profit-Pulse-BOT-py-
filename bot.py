import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import aiosqlite
import os
import sys
import aiohttp
import asyncio
import random
import string
import numpy as np
import io
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import datetime
from datetime import date, datetime, timedelta
import pnwkit
import concurrent.futures
from db import DatabaseUser

# Load environment variables
load_dotenv()
TOKEN = os.getenv('TOKEN')
PNW_API_KEY = os.getenv('PNW_API_KEY')
kit = pnwkit.QueryKit(PNW_API_KEY)
LOG_CHANNEL_ID = 1283031424399839263
verification_codes={}
AUTHORIZED_ROLE_ID = int(os.getenv('AUTHORIZED_ROLE_ID'))

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
db = DatabaseUser()


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}!')
    await db.init_db()
    await bot.tree.sync()
    update_share_prices.start()
async def log_transaction(company_name: str, num_shares: int, share_price: float, total_value: float, user_id: str, transaction_type: str):
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    
    if log_channel:
        log_message = (
            f"{transaction_type} Transaction Log:\n"
            f"User ID: {user_id}\n"
            f"Company: {company_name}\n"
            f"Shares {transaction_type}d: {num_shares}\n"
            f"Share Price: {share_price}\n"
            f"Total {transaction_type} Value: {total_value}\n"
            f"Time: {datetime.now().isoformat()}"
        )
        await log_channel.send(log_message)
    else:
        print(f"Log channel with ID {LOG_CHANNEL_ID} not found.")
        

async def generate_graph_in_background(company_name, times, prices, period):
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, lambda: create_and_save_graph(company_name, times, prices, period))

def create_and_save_graph(company_name, times, prices, period):
    # Create the plot
    plt.figure(figsize=(10, 5))

    # Plot with different colors based on price direction
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1]:
            plt.plot([times[i-1], times[i]], [prices[i-1], prices[i]], color='green', linewidth=2, marker='o')
        else:
            plt.plot([times[i-1], times[i]], [prices[i-1], prices[i]], color='red', linewidth=2, marker='o')

    # Format and style the graph
    plt.title(f"Share Price History for {company_name} ({period})")
    plt.xlabel('Time')
    plt.ylabel('Share Price')
    plt.grid(True)
    plt.xticks(rotation=45)

    # Adjust the x-axis to show only key time points, not cluttered data
    if period in ['1h', '12h']:
        plt.gca().xaxis.set_major_locator(plt.MaxNLocator(10))  # Adjust for shorter periods
    elif period in ['1d', '3d', '7d']:
        plt.gca().xaxis.set_major_locator(plt.MaxNLocator(6))  # Adjust for longer periods

    # Save the plot to a BytesIO object
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()

    return buf

@bot.tree.command(name="share_price_graph", description="Get a graph of share prices over a specific period.")
@app_commands.describe(company_name="Graph of the company", period="1h,12h,1d,3d,7d")
async def share_price_graph(interaction: discord.Interaction, company_name: str, period: str):
    await interaction.response.defer()
    try:
        price_data = await db.get_share_price_history(company_name, period)

        if not price_data:
            await interaction.followup.send(f"No price history found for {company_name}.", ephemeral=True)
            return

        times, prices = zip(*[(time, price) for date, time, price in price_data])

        buf = await generate_graph_in_background(company_name, times, prices, period)
        file = discord.File(fp=buf, filename=f"{company_name}_price_history.png")
        await interaction.followup.send(file=file)

    except Exception as e:
        await interaction.followup.send(f"An error occurred while generating the graph: {str(e)}", ephemeral=True)

@tasks.loop(minutes=1)
async def update_share_prices():
    # Fetch all companies
    companies = await db.get_all_companies()
    
    # Loop through all companies and record their current prices
    for company in companies:
        company_name = company[0]
        share_price = company[1]
        current_date = date.today().isoformat()
        current_time = datetime.now().strftime('%H:%M:%S')
        await db.store_share_price_history(company_name, current_date, current_time, share_price)

@bot.tree.command(name="ping", description="-")
async def ping(interaction: discord.Interaction):
    latency = bot.latency * 1000
    await interaction.response.send_message(f"pong! {latency:.1f}ms")

@bot.tree.command(name="who", description="Get nation information from Politics and War.")
@app_commands.describe(nation="Provide a nation ID, nation name, or mention a user to fetch their nation information.")
async def who(interaction: discord.Interaction, nation: str):
    # Check if the identifier is a mention
    if nation.startswith("<@") and nation.endswith(">"):
        try:
            user_id = int(nation[2:-1])  # Convert mention to user ID
        except ValueError:
            await interaction.response.send_message("Invalid user mention.", ephemeral=True)
            return
        
        # Get the user's nation ID from the database
        nation_id = await db.get_user_data_by_user_id(user_id)
        if not nation_id:
            await interaction.response.send_message(f"User <@{user_id}> has not verified their nation ID yet. Please ask them to use the /verify command first.", ephemeral=True)
            return
    else:
        # Try to determine if the identifier is a nation ID (numeric) or nation name (string)
        if nation.isdigit():
            nation_id = int(nation)
        else:
            # Fetch nation by name
            query = kit.query("nations", {"nation_name": nation}, "id, nation_name")
            result = query.get()
            if not result or not result.nations:
                await interaction.response.send_message("Failed to fetch nation data by name. Please check the nation name and try again.", ephemeral=True)
                return
            nation_id = result.nations[0].id

    # Fetch the nation information from the Politics and War API using the nation ID
    query = kit.query("nations", {"id": int(nation_id)}, "id, nation_name")
    result = query.get()

    if not result or not result.nations:
        await interaction.response.send_message("Failed to fetch nation data. Please try again later.", ephemeral=True)
        return

    nation_name = result.nations[0].nation_name

    # Fetch balance and company shares information
    if await db.get_user_data_by_nation_id(nation_id):
        balance = round(await db.get_user_credits(await db.get_user_data_by_nation_id(nation_id)),2)
        companies = await db.get_all_companies()  # Assuming this returns a list of companies

        user_shares_info = ""  # This will store all the companies and shares info
        total_worth = 0  # To store the total worth of shares for all companies

        for company_data in companies:
            # Extract company name, share price, etc. Assuming company_data is structured like (company_name, share_price, ...)
            if isinstance(company_data, tuple):
                company_name = company_data[1]  # Extract the company name
                share_price = company_data[2]  # Extract the share price
            else:
                company_name = str(company_data)
                share_price = 0  # In case share price is missing, default to 0

            user_shares = await db.get_user_shares(user_id, company_name)
            
            # Calculate worth of the user's shares in this company
            company_worth = user_shares * share_price
            total_worth = (total_worth) + (company_worth)
            # Add the company and shares info to the user_shares_info string

            if user_shares > 0:
                user_shares_info += (
                    f"🏢 **{company_name}**\n"
                    f"📊 **Shares Owned**: {user_shares}\n"
                    f"💰 **Worth**: ${company_worth:,.2f}\n"
                    f"🔖 **Share Price**: ${share_price:,.2f}\n\n"
                )
    else:
        balance = 'Not Registered'
        user_shares_info = 'No shares registered.'
        total_worth = 0

    # Create the first embedded message for nation details and balance
    embed1 = discord.Embed(title="Nation Information", color=discord.Color.blue())
    embed1.add_field(name="🌍 Nation Name", value=f"[{nation_name}](<https://politicsandwar.com/nation/id={nation_id}>)", inline=False)
    
    if balance != 'Not Registered':
        embed1.add_field(name="💵 Balance", value=f'<:dollar:1312376430788870194> {balance:,}', inline=False)
        embed1.add_field(name="💼 Total Shares Worth", value=f'<:dollar:1312376430788870194> {total_worth:,}', inline=False)
    else:
        embed1.add_field(name="💵 Balance", value=balance, inline=False)

    # Create the second embedded message for company shares details
    embed2 = discord.Embed(title="Company Shares", color=discord.Color.green())
    embed2.add_field(name="💡 Shares Info", value=user_shares_info or "No shares available", inline=False)

    # Send both embedded messages
    await interaction.response.send_message(embeds=[embed1, embed2])

@bot.tree.command(name="help", description="Shows a list of available commands.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Available Commands",
        description="Here are the commands you can use:",
        color=discord.Color.blue()
    )

    for command in bot.tree.get_commands():
        embed.add_field(
            name=f"/{command.name}",
            value=command.description or "No description",
            inline=False
        )

    try:
        await interaction.response.send_message(embed=embed)
    except discord.errors.InteractionResponded:
        print("Interaction has already been responded to.")

@bot.tree.command(name="verify", description="Verify your nation ID.")
async def verify_command(interaction: discord.Interaction, nation_id: int):
    user_id = str(interaction.user.id)

    # Check if the user is already registered
    stored_nation_id = await db.get_user_data_by_user_id(user_id)

    if stored_nation_id:
        await interaction.response.send_message(
            f"You are already registered with Nation ID: {stored_nation_id}.",
            ephemeral=True
        )
        return

    user = interaction.user.name
    query = kit.query("nations", {"id": int(nation_id)}, "id, nation_name, discord")
    result = query.get()

    if not result or not result.nations:
        await interaction.response.send_message("Failed to fetch nation data. Please try again later.", ephemeral=True)
        return

    nation_name = result.nations[0].nation_name
    discord = result.nations[0].discord
    
    if user == discord:
        # Store nation data in db
        await db.add_user(user_id, nation_id)
        await interaction.response.send_message("Registered successfully!")
    else:
        await interaction.response.send_message(f"Your nation Discord ({discord}) does not match your username ({user}).", ephemeral=True)


@bot.tree.command(name="add_credits", description="Add credits to a user's account.")
async def add_credits(interaction: discord.Interaction, user: discord.User, amount: float):
    # Check if the command invoker has the authorized role
    if AUTHORIZED_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    command_user_registered = await db.get_user_data_by_user_id(interaction.user.id)
    mentioned_user_registered = await db.get_user_data_by_user_id(user.id)

    if not command_user_registered:
        await interaction.response.send_message("You are not registered with the bot. Please register first.", ephemeral=True)
        return

    if not mentioned_user_registered:
        await interaction.response.send_message(f"The mentioned user {user.mention} is not registered with the bot.", ephemeral=True)
        return

    # Add credits to the user
    await db.add_credits(user.id, amount)
    await interaction.response.send_message(f"Added {amount} credits to {user.mention}'s account.")

@bot.tree.command(name="register_company", description="Register a new company.")
@app_commands.describe(company_name="Name of the company", owner="Who owns the company", share_price="Initial share price", total_shares="Total number of shares")
async def register_company(interaction: discord.Interaction, company_name: str, owner:discord.User, share_price: float, total_shares: int):
    # Check if the company already exists
    existing_company = await db.get_company(company_name)
    if not any(role.id==AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to remove.", ephemeral=True)
        return
    if existing_company:
        await interaction.response.send_message(f"Company `{company_name}` already exists.", ephemeral=True)
        return

    # Add the company to the database
    await db.add_company(company_name, share_price, total_shares, owner.id)
    await db.add_shares(company_name, total_shares)
    await interaction.response.send_message(f"Company `{company_name}` registered successfully with {total_shares} shares at {share_price} coins per share.")

@bot.tree.command(name="list_companies", description="List all registered companies.")
async def list_companies(interaction: discord.Interaction):
    
    companies = await db.get_all_companies()
    
    if not companies:
        await interaction.response.send_message("No companies are currently registered.", ephemeral=True)
        return

    # Send the first response with the first company embed or a simple acknowledgment
    await interaction.response.defer()

    for i, (company_id, company_name, share_price, total_shares, user_id) in enumerate(companies):
        shares = await db.get_shares(company_name)
        percent = round((total_shares / shares) * 100, 2) if shares else 0
        valuation = round(shares * share_price, 2) if shares else 0

        # Fetch dividend info for the company
        dividends = await db.get_dividends(company_name)
        dividend_info = ""
        
        if dividends:
            for dividend_per_share, payout_date in dividends:
                dividend_info += f"**Dividend**: ${dividend_per_share:,} (Payout Date: {payout_date})\n"
        else:
            dividend_info = "**Dividends**: No dividends posted.\n"

        # Create a new embed for each company
        embed = discord.Embed(title=f"Company ID: {company_id} - Company Name: {company_name}", color=discord.Color.blue())
        embed.add_field(name="Share Price", value=f"<:dollar:1312376430788870194>{share_price:,}", inline=False)
        embed.add_field(name="Registered Shares", value=f"{shares}", inline=True)
        embed.add_field(name="Remaining Shares", value=f"{total_shares} ({percent}%)", inline=True)
        embed.add_field(name="Company Valuation", value=f"${valuation:,}", inline=False)
        embed.add_field(name="Owner", value=f"<@" + str(user_id) + ">", inline=False)
        embed.add_field(name="Dividends", value=dividend_info, inline=False)
        
        # Send the first message using interaction.followup.send
        if i == 0:
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(embed=embed)

@bot.tree.command(name="buy_shares", description="Buy shares in a company.")
@app_commands.describe(company_name="Company name", company_id="Company ID", num_shares="Number of shares you will buy")
async def buy_shares(interaction: discord.Interaction, company_name: str = None, company_id: str = None, num_shares: int = None):
    user_id = interaction.user.id
    try:
        # Fetch the company details
        if company_name:
            company = await db.get_company(company_name=company_name)
        elif company_id:
            if company_id.isdigit():  # Check if company ID is a valid integer string
                company = await db.get_company(company_id=int(company_id))
            else:
                await interaction.response.send_message("Invalid company ID.", ephemeral=True)
                return
        else:
            await interaction.response.send_message("Please enter either a company name or ID.", ephemeral=True)
            return

        if not company:
            await interaction.response.send_message("Invalid company ID or name.", ephemeral=True)
            return
    
    
        company_name, share_price, total_shares, company_owner_id = company

        MAX_SHARES_PER_TRANSACTION = 999999999
        if num_shares > MAX_SHARES_PER_TRANSACTION:
            await interaction.response.send_message(f"Cannot buy more than {MAX_SHARES_PER_TRANSACTION} shares in a single transaction.", ephemeral=True)
            return
        
        if total_shares < num_shares:
            await interaction.response.send_message(f"Not enough shares available. Available Shares: {total_shares}", ephemeral=True)
            return

        share_price = round(float(share_price), 2)
        total_cost = round(num_shares * share_price, 2)

        # Check if the user has enough balance
        user_balance = await db.get_user_credits(user_id)
        if user_balance < total_cost:
            await interaction.response.send_message("You don't have enough coins to buy these shares.", ephemeral=True)
            return

        # Update the user's shares and balance
        await db.update_user_shares(user_id, company_name, num_shares)
        await db.update_user_credits_after_purchase(user_id, total_cost)

         # Get the current timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        #insert into Share Price table
        await db.insert_share_price_history(company_name, share_price, timestamp)

        # Update the company's balance (add to the owner's credits)
        await db.add_credits(company_owner_id, total_cost)

        # Fetch the average price of the company's shares
        average_price = await db.get_average_price(company_name)
        if average_price is None:
            average_price = await db.get_average_price_all_trades(company_name)

        #Update the company's details
        new_price = average_price
        new_shares = total_shares - num_shares
        await db.update_company_details(company_name, new_price, new_shares)

        # Log the transaction to a specific Discord channel
        await log_transaction(company_name, num_shares, share_price, total_cost, user_id, "Buy")

        await interaction.response.send_message(f"Successfully bought {num_shares} shares of {company_name} for {total_cost} coins.")
    
    except Exception as e:
        # Handle any errors that occur during the transaction
        await interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)

@bot.tree.command(name="sell_shares", description="Sell shares of a company.")
@app_commands.describe(company_name="Name of the company to sell shares from.", num_shares="Number of shares you want to sell")
async def sell_shares(interaction: discord.Interaction, company_name: str, num_shares: int):
    user_id = interaction.user.id

    # Fetch the company details
    company = await db.get_company_D(company_name)
    if not company:
        await interaction.response.send_message("Invalid company name.", ephemeral=True)
        return

    company_name, share_price, total_shares, company_owner_id = company
    share_price = round(float(share_price), 2)

    # Check if the user has enough shares to sell
    user_shares = await db.get_user_shares(user_id, company_name)
    if user_shares is None or user_shares < num_shares:
        await interaction.response.send_message("You don't have enough shares to sell.", ephemeral=True)
        return
    MAX_SHARES_PER_TRANSACTION = 0
    if num_shares > MAX_SHARES_PER_TRANSACTION:
        await interaction.response.send_message(f"Cannot sell more than {MAX_SHARES_PER_TRANSACTION} shares in a single transaction.", ephemeral=True)
        return

    # Calculate the total value of the shares being sold
    total_value = round(num_shares * share_price, 2)

    # Update the user's shares and balance
    await db.update_user_shares(user_id, company_name, -num_shares)
    await db.add_credits(user_id, total_value)

    # Update the company's balance (deduct from the owner's credits)
    await db.update_user_credits_after_purchase(company_owner_id, total_value)

    # Reduce the share price slightly when shares are sold
    if total_shares == 0:
        new_price = round(share_price * 0.90, 2)
    else:
        new_price = round(share_price * (1 + (num_shares / total_shares) ** 1.1), 2)-share_price
        new_price = share_price - max(30, new_price)

    new_shares = total_shares + num_shares
    await db.update_company_details(company_name, new_price, new_shares)

    # Log the transaction in the specified channel
    await log_transaction(company_name, -num_shares, share_price, total_value, user_id, "Sell")

    await interaction.response.send_message(f"Successfully sold {num_shares} shares of {company_name} for {total_value} coins.")
    
@bot.tree.command(name="remove_company", description="Remove a company from the database.")
@app_commands.describe(company_name="The name of the company to remove.")
async def remove_company_command(interaction: discord.Interaction, company_name: str):
    company = await db.get_company(company_name)
    if not any(role.id==AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to remove.", ephemeral=True)
        return
    if not company:
        await interaction.response.send_message(f"Company {company_name} does not exist.")
        return

    await db.remove_company(company_name)
    await interaction.response.send_message(f"Company {company_name} and all related data have been removed.")
    
@bot.tree.command(name="edit_company", description="Edit company details.")
@app_commands.describe(company_name="Name of the company to edit", new_share_price="New share price", new_total_shares="New total number of shares")
async def edit_company(interaction: discord.Interaction, company_name: str, new_share_price: float, new_total_shares: int):
    # Check if the company exists
    company = await db.get_company(company_name)
    if not any(role.id==AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to remove.", ephemeral=True)
        return
    if not company:
        await interaction.response.send_message(f"Company `{company_name}` does not exist.", ephemeral=True)
        return

    # Update the company's share price and total shares
    await db.update_company_details(company_name, new_share_price, new_total_shares)

    await interaction.response.send_message(f"Company `{company_name}` updated successfully. New price: {new_share_price} coins, New total shares: {new_total_shares}.")

@bot.tree.command(name="update_registered_shares",description="Updates the registered shares")
@app_commands.describe(company_name="Name of the company to edit", shares='Shares of the company')
async def update_registered_shares(interaction: discord.Interaction, company_name: str, shares: int):
    company = await db.get_company(company_name)
    if not any(role.id==AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to remove.", ephemeral=True)
        return
    if not company:
        await interaction.response.send_message(f"Company `{company_name}` does not exist.", ephemeral=True)
        return
    await db.add_shares(company_name, shares)
    await interaction.response.send_message('Updated!')

@bot.tree.command(name='market', description="Show all available trades.")
async def market(interaction: discord.Interaction):
    trades = await db.get_all_trades()  # Fetch all available trades from the database

    if not trades:
        await interaction.response.send_message("No trades available.")
        return

    # Create an embed for displaying trades
    embed = discord.Embed(title="Available Trades", color=discord.Color.blue())

    for trade in trades:
        # Assuming the tuple is structured as (trade_id, seller_id, company_name, shares_available, price_per_share)
        trade_id = trade[0]
        seller_id = str(trade[1])
        company_name = trade[2]
        shares_available = trade[3]
        price_per_share = round(trade[4],2)

        embed.add_field(
            name=f"Trade ID: {trade_id}",
            value=(
                f"**Seller ID:** {'<@' + seller_id + '>'}\n"
                f"**Company:** {company_name}\n"
                f"**Shares Available:** {shares_available}\n"
                f"**Price per Share:** ${price_per_share:,}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="post_trade", description="Post a trade to sell shares on the market")
@app_commands.describe(company="Company to sell shares from", shares="Number of shares", price="Price per share", to="(Optional) User to send a direct trade to")
async def post_trade(interaction: discord.Interaction, company: str, shares: int, price: float, to: discord.User = None):
    user_id = interaction.user.id
    
    # Check if the user owns enough shares
    user_shares = await db.get_user_shares(user_id, company)
    if user_shares < shares:
        await interaction.response.send_message(f"You don't have enough shares to sell. You currently own {user_shares} shares of {company}.", ephemeral=True)
        return

    # If 'to' user is specified, it's a direct trade
    to_user_id = to.id if to else None

    # Post the trade in the market
    await db.create_trade(company, user_id, shares, price, to_user_id)
    
    if to_user_id:
        await interaction.response.send_message(f"Direct trade posted: Selling {shares} shares of {company} at ${price:,} per share to {to.mention}.")
    else:
        await interaction.response.send_message(f"Trade posted: Selling {shares} shares of {company} at ${price:,} per share.")

@bot.tree.command(name="buy_trade", description="Buy shares from the market")
@app_commands.describe(trade_id="ID of the trade to buy", num_shares="Number of shares to buy")
async def buy_trade(interaction: discord.Interaction, trade_id: int, num_shares: int):
    buyer_id = interaction.user.id
    
    # Get the trade details from the database
    trade = await db.get_trade(trade_id)
    if not trade:
        await interaction.response.send_message("Trade not found. Please check the trade ID.", ephemeral=True)
        return
    
    company_name = trade['company_name']
    shares_available = trade['shares_available']
    price_per_share = trade['price_per_share']
    seller_id = trade['seller_id']
    to_user_id = trade['to_user_id']  # New field to specify the direct trade recipient

    # If the trade is restricted to a specific user, check if the buyer is that user
    if to_user_id and buyer_id != to_user_id:
        await interaction.response.send_message(f"This trade is only available to <@{to_user_id}>.", ephemeral=True)
        return

    # insert share price table
    await db.insert_share_price_history(company_name, price_per_share, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    # Check if the requested number of shares is available
    if num_shares > shares_available:
        await interaction.response.send_message(f"Not enough shares available. Only {shares_available} shares are left.", ephemeral=True)
        return

    total_cost = num_shares * price_per_share

    # Check if the buyer has enough credits
    buyer_credits = await db.get_user_credits(buyer_id)
    if buyer_credits < total_cost:
        await interaction.response.send_message(f"You don't have enough credits to buy {num_shares} shares of {company_name}. Total cost is ${total_cost:,}.")
        return
    
    # Transfer credits and shares
    await db.add_credits(buyer_id, -total_cost)
    await db.add_credits(seller_id, total_cost)
    await db.update_user_shares(seller_id, company_name, -num_shares)
    await db.update_user_shares(buyer_id, company_name, num_shares)
    # Update or remove the trade from the market
    remaining_shares = shares_available - num_shares
    if remaining_shares > 0:
        # Update the trade to reflect the new number of available shares
        await db.update_trade(trade_id, remaining_shares)
    else:
        # Remove the trade from the market if no shares are left
        await db.delete_trade(trade_id)

    await interaction.response.send_message(f"Successfully bought {num_shares} shares of {company_name} from <@{seller_id}> for ${total_cost:,}.")

    # Fetch the company details
    company = await db.get_company(company_name)
    company_name, share_price, total_shares, company_owner_id = company
    
    # Fetch the average price of the company's shares
    average_price = await db.get_average_price(company_name)
    if average_price is None:
        average_price = await db.get_average_price_all_trades(company_name)
    
    #Update the company's details
    new_price = average_price
    new_shares = total_shares
    await db.update_company_details(company_name, new_price, new_shares)

@bot.tree.command(name="post_dividend", description="Post a dividend payout for a company")
@app_commands.describe(company="Company to post dividends for", dividend="Dividend amount per share", payout_date="Payout date (YYYY-MM-DD)")
async def post_dividend(interaction: discord.Interaction, company: str, dividend: float, payout_date: str):
    # Check if the user has the required permissions
    if not any(role.id == AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to post dividends.", ephemeral=True)
        return
    
    # Insert dividend into the dividends table using the database method
    await db.post_dividend(company, dividend, payout_date)
    
    await interaction.response.send_message(f"Dividend posted: ${dividend:.2f} per share for {company}, to be paid on {payout_date}.", ephemeral=True)

@bot.tree.command(name="distribute_dividends", description="Distribute dividends for a company")
async def distribute_dividends(interaction: discord.Interaction, company: str):
    if not any(role.id == AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to distribute dividends.", ephemeral=True)
        return

    # Get dividends due for the specified company
    due_dividends = await db.get_due_dividends(company)

    if not due_dividends:
        await interaction.response.send_message(f"No dividends due for {company} to distribute.", ephemeral=True)
        return

    # Distribute dividends
    for dividend in due_dividends:
        # Safely unpack the dividend values
        dividend_per_share, payout_date = dividend[:2]

        await db.distribute_dividends(company)  # This should handle the distribution logic
        await interaction.response.send_message(f"Dividends per share {dividend_per_share}, date: {payout_date}.", ephemeral=True)
        # After distributing, delete the dividend record
        await db.delete_dividend(company, payout_date)

    await interaction.response.send_message(f"Dividends distributed for {company}.", ephemeral=True)

@bot.tree.command(name="remove_dividend", description="Remove a dividend payout for a company")
@app_commands.describe(company="Company to remove dividends from", payout_date="Payout date (YYYY-MM-DD)")
async def remove_dividend(interaction: discord.Interaction, company: str, payout_date: str):
    # Check if the user has the required permissions
    if not any(role.id == AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to remove dividends.", ephemeral=True)
        return

    # Remove the dividend from the database
    await db.delete_dividend(company, payout_date)

    await interaction.response.send_message(f"Dividend for {company} on {payout_date} has been removed.", ephemeral=True)
@bot.tree.command(name="change_company_owner", description="Change the owner of a company.")
@app_commands.describe(company="Company to change ownership of", new_owner="New owner's Discord")
async def change_company_owner(interaction: discord.Interaction, company: str, new_owner: discord.User):
    # Check if the user has the required permissions
    if not any(role.id == AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permissions to change the company owner.", ephemeral=True)
        return

    # Check if the company exists
    existing_company = await db.get_company(company)
    if not existing_company:
        await interaction.response.send_message(f"The company '{company}' does not exist.", ephemeral=True)
        return

    # Change the owner in the database
    await db.update_company_owner(company, new_owner.id)

    await interaction.response.send_message(f"The owner of '{company}' has been changed to <@{new_owner}>.", ephemeral=True)

@bot.tree.command(name="test")
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("Test command executed.", ephemeral=True)

@bot.tree.command(name="add_security_depo",description="Add company security deposit")
@app_commands.describe(amount="How much deposit they are giving", company="Company name")
async def add_security_depo(interaction: discord.Interaction, amount:str, company:str):
    try:
        if not any(role.id == AUTHORIZED_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("You do not have permissions to change the company owner.", ephemeral=True)
            return 
        existing_company = await db.get_company(company)
        if not existing_company:
            await interaction.response.send_message(f"The company '{company}' does not exist.", ephemeral=True)
            return
        await db.add_depo(company,amount)
        await interaction.response.send_message(f"Company: {company}\nSecurity Deposit: {amount}")
    except Exception as e:
        await interaction.response.send_message(e, ephemeral=True)


@bot.tree.command(name="edit_trade", description="Edit a trade")
@app_commands.describe(trade_id="ID of the trade to edit", shares_available="New number of shares available", price_per_share="New price per share")
async def edit_trade(interaction: discord.Interaction, trade_id: int, shares_available: int = None, price_per_share: float = None):
    try:
        trade = await db.get_trade_by_id(trade_id)
        if not trade:
            await interaction.response.send_message(f"Trade {trade_id} not found!")
            return
        if trade[1] != interaction.user.id:
            await interaction.response.send_message("You are not the seller of this trade!")
            return
        await db.update_trade(trade_id, shares_available, price_per_share)
        await interaction.response.send_message(f"Trade {trade_id} updated successfully!")
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {str(e)}")

@bot.tree.command(name="delete_trade", description="Delete a trade")
@app_commands.describe(trade_id="ID of the trade to delete")
async def delete_trade(interaction: discord.Interaction, trade_id: int):
    trade = await db.get_trade_by_id(trade_id)
    if not trade:
        await interaction.response.send_message(f"Trade {trade_id} not found!")
        return
    if trade[1] != interaction.user.id:
        await interaction.response.send_message("You are not the seller of this trade!")
        return
    try:
        await db.delete_trade(trade_id)
        await interaction.response.send_message(f"Trade {trade_id} deleted successfully!")
    except ValueError as e:
        await interaction.response.send_message(str(e))

@bot.tree.command(name="shareholders_info", description="Get the shareholders of a company")
@app_commands.describe(company_name="Company name", company_id="Company ID")
async def shareholders(interaction: discord.Interaction, company_name: str = None, company_id: str = None):
    try:
        # Fetch the company details
        if company_name:
            company = await db.get_company(company_name=company_name)
        elif company_id:
            if company_id.isdigit():  # Check if company ID is a valid integer string
                company = await db.get_company(company_id=int(company_id))
            else:
                await interaction.response.send_message("Invalid company ID.", ephemeral=True)
                return
        else:
            await interaction.response.send_message("Please enter either a company name or ID.", ephemeral=True)
            return

        if not company:
            await interaction.response.send_message("Invalid company ID or name.", ephemeral=True)
            return

        company_name, share_price, total_shares, company_owner_id = company

        # Get the shareholders
        shareholders = await db.get_shareholders(company_name)

        # Create an embed to display the shareholders
        embed = discord.Embed(title=f"Shareholders of {company_name}", color=discord.Color.blue())
        for shareholder in shareholders:
            user_id = shareholder[0]
            user = await interaction.client.fetch_user(user_id)
            shares = shareholder[2]
            embed.add_field(name=f"{user.name} (<@{user_id}>)", value=f"Shares: {shares}", inline=False)

        await interaction.response.send_message(embed=embed)
    except Exception as e:
        print(f"Error: {e}")
        await interaction.response.send_message(f"An error occurred.{e}", ephemeral=True)

@bot.tree.command(name="restart", description="Restart the bot")
async def restart(interaction: discord.Interaction):
    if not any(role.id == AUTHORIZED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permission to restart the bot.", ephemeral=True)
        return
    await interaction.response.send_message("Restarting the bot...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

bot.run(TOKEN)