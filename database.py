from datetime import datetime
import motor.motor_asyncio
import os
import uuid
import images
import utils

dbuser = os.getenv('dbuser')
dbpassword = os.getenv('dbpassword')

connection_uri = f'mongodb+srv://{dbuser}:{dbpassword}@cluster0-2ixzl.mongodb.net/?retryWrites=true&w=majority'

client = motor.motor_asyncio.AsyncIOMotorClient(connection_uri)

db = client['repldex']

entries_coll = db['entries']
sessions_coll = db['sessions']
users_coll = db['users']

async def fix_entry(data):
	if data is None: return
	original_data = dict(data)
	if data.get('image') and isinstance(data['image'], str):
		data['image'] = data['image'].replace('imag.cf', 'i.matdoes.dev')
	if data.get('image') and isinstance(data.get('image'), str):
		data['image'] = await images.get_data(data['image'])
	elif data.get('image') and not data['image'].get('thumbnail_b64'):
		data['image'] = await images.get_data(data['image']['src'])
	if data != original_data:
		# print('updated', data['_id'])
		await entries_coll.update_one(
			{'_id': data['_id']},
			{
				'$set': data
			}
		)
	data['content'] = utils.fix_html(data['content'])
	if 'nohtml_content' not in data:
		data['nohtml_content'] = utils.remove_html(data['content'])
	return data

async def edit_entry(title, content, editor=None, unlisted=False, entry_id=None, image=None):
	t = datetime.now()
	title = title.strip()
	content = utils.fix_html(content)
	nohtml_content = utils.remove_html(content)
	new_data = {
		'title': title,
		'content': content,
		'last_edited': t,
		'nohtml_content': nohtml_content
	}
	if unlisted is not None:
		new_data['unlisted'] = unlisted
	if image is not None:
		new_data['image'] = {
			'src': image
		}

	if not entry_id:
		entry_id = str(uuid.uuid4())
	new_history_data = {
		'author': editor,
		'content': content,
		'title': title,
		'time': t,
		'unlisted': unlisted,
	}
	if image is not None:
		new_history_data['image'] = {
			'src': image
		}
	await entries_coll.update_one(
		{'_id': entry_id},
		{
			'$set': new_data,
			'$push': {
				'history': new_history_data
			}
		},
		upsert=True
	)
	return entry_id

async def get_entry(entry_id=None, name=None, search_id=True, owner=None):
	if not entry_id and name:
		entries = await search_entries(
			name,
			limit=1,
			search_id=search_id
		)
		if not entries: return
		return entries[0]
	elif owner:
		# print(type(owner), owner)
		found = await entries_coll.find_one({
			'owner_id': owner
		})
	else:
		found = await entries_coll.find_one({
			'_id': entry_id
		})
	found = await fix_entry(found)
	return found

async def new_editor_session(discord_id):
	sid = str(uuid.uuid4())
	await sessions_coll.insert_one(
		{
			'_id': sid,
			'discord': discord_id,
			'time': datetime.now()
		}
	)
	return sid

async def get_editor_session(sid):
	if not hasattr(get_editor_session, 'cache'):
		get_editor_session.cache = {}
	if sid in get_editor_session.cache:
		found = get_editor_session.cache[sid]
	else:
		found = await sessions_coll.find_one({
			'_id': sid
		})
		get_editor_session.cache[sid] = found
	if found is None: return
	return found['discord']

async def search_entries(query, limit=10, search_id=True, page=0):
	found = []
	async for doc in entries_coll.aggregate([
		{'$searchBeta': {
			'compound': {
				'should': [
					{'search': {
						'query': query,
						'path': 'nohtml_content'
					}},
					{'search': {
						'query': query,
						'path': 'title',
						'score': {
							'boost': {
								'value': 20
							}
						}
					}}
				]
			}
		}},
		{'$match': {
			'unlisted': {'$ne': True}
		}},
		{'$addFields': {
      'score': {
				'$meta': 'searchScore'
			}
    }},
		{'$sort': {
			'score': -1
		}},
		{'$skip': page * limit},
		{'$limit': limit}
	]):
		# print(doc['title'], doc['score'])
		found.append(await fix_entry(doc))
	if len(found) == 0 and search_id:
		found = await get_entry(query)
		if found: return [found]
		if found is None: found = []
	if len(found) == 0:
		searched = await entries_coll.find_one({
			'title': query
		})
		if searched:
			found = [searched]
	return found

# Query is only if sort == relevant
async def get_entries(sort, limit=20, page=0, query=None):
	if sort == 'relevant' and query:
		found = await search_entries(query, limit=limit, page=page)
		return found
	cursor = entries_coll.find({
		'unlisted': {'$ne': True}
	})
	cursor = cursor.sort(sort, -1)
	cursor = cursor.skip(page * limit)
	cursor = cursor.limit(limit)
	found = []
	async for entry in cursor:
		entry = await fix_entry(entry)
		found.append(entry)
	return found

async def set_personal_entry(discord_id, entry_id):
	user_data = {
		'personal_entry': entry_id,
	}
	await users_coll.update_one(
		{
			'_id': discord_id
		},
		{
			'$set': user_data
		},
		upsert=True
	)
	async for entry in entries_coll.find({'owner_id': discord_id}):
		await entries_coll.update_one(
			{'_id': entry['_id']},
			{
				'$set': {
					'owner_id': None
				}
			}
		)
	await entries_coll.update_one(
		{'_id': entry_id},
		{
			'$set': {
				'owner_id': discord_id
			}
		}
	)
	try:
		if hasattr(get_personal_entry, 'cache'):
			get_personal_entry.cache[discord_id] = user_data
	except Exception as e:
		print('BRUH MOMENT',e)

async def get_personal_entry(discord_id):
	if not hasattr(get_personal_entry, 'cache'):
		get_personal_entry.cache = {}
	if discord_id in get_personal_entry.cache:
		found = get_personal_entry.cache[discord_id]
	else:
		found = await users_coll.find_one({
			'_id': discord_id
		})
		get_personal_entry.cache[discord_id] = found
	if found is None: return
	return found.get('personal_entry')

async def count_entries():
	count = await entries_coll.count_documents({
		'unlisted': {'$ne': True}
	})
	return count

async def get_random_entry():
	# cursor = entries_coll.find({
	# 	'unlisted': {'$ne': True}
	# })
	# cursor = cursor.sort(sort, -1)
	# cursor = cursor.skip(page * limit)
	# cursor = cursor.limit(limit)
	cursor = entries_coll.aggregate([
		{'$match': {
			'unlisted': {'$ne': True}
		}},
		{'$sample': {
			'size': 1
		}}
	])
	found = []
	async for entry in cursor:
		found.append(entry)
	return found[0]
