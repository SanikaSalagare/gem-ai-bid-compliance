BUYERS = [
    {"id": "buyer_001", "name": "Government Procurement Cell", "organization": "Public Sector Buyer"},
    {"id": "buyer_002", "name": "Central IT Procurement", "organization": "Government Buyer"},
    {"id": "buyer_003", "name": "Infrastructure Procurement Division", "organization": "Government Buyer"},
]

SELLERS = [
    {"id": "seller_001", "name": "Apex Technologies Pvt. Ltd.", "organization": "Registered Seller"},
    {"id": "seller_002", "name": "Bharat Systems & Solutions", "organization": "Registered Seller"},
    {"id": "seller_003", "name": "Nova Office & IT Supplies", "organization": "Registered Seller"},
]


def get_account(accounts, account_id):
    return next((account for account in accounts if account["id"] == account_id), accounts[0])
