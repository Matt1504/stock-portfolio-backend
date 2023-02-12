# Stock Portfolio Backend

## Overview
This project is the code that runs the backend server for our front end Stock Portfolio application. It connects to a MongoDB database that stores our data. API requests are using GraphQL and handled with Graphene-Python. The web server application is hosted using Flask. 

## Getting started
Set up Mongo DB [account](https://www.mongodb.com/cloud/atlas/register)

Set up your cluster

Create passwords.py in directory src/database/passwords.py and add your USER, PASSWORD, and CLUSTER variables in the following format 
```python
USER = "USER_NAME"
PASSWORD = "PASSWORD_VALUE"
CLUSTER = "CLUSTER_NAME"
```
Make sure you have your python virtual environment set up 

Start your Virtual Environment
```bash
source src/bin/activate
```

Install the following libraries

```bash
brew tap mongodb/brew
brew install mongodb-community
pip install -r requirements.txt
```

Set up and Flask App
```bash
export FLASK_APP=app.py
```
## Initializing your Data
By default, your database should be empty with no collections. We will run database_init.py to seed your database with data from startup.json. Running this will delete your current database and create the necessary collections

```bash
python3 database_init.py
```

The startup.json file will be all your loading data to populate your collections. It is composed of the following data
- Currency (US, CAD currently)
- Accounts (TFSA and RRSP, with FHSA coming when it is officially released)
- Platforms (The trading platforms that you use, ex: TD Direct Investing, Wealthsimple)
- Activities (All the possible transaction activities, ex: Contribution, Buy, Sell, Dividends, etc.)

Below is a piece of the startup.json that shows the currencies model

```json
{
    "currencies": [
        {
            "name": "Canadian Dollar",
            "code": "CAD"
        },
        {
            "name": "United States Dollar",
            "code": "USD"
        }
    ],
}
```

## Running Web Server
Once you are fully setup you can start your local web server
```bash
python3 app.py
```
This will run your server on the URL [http://127.0.0.1:5000/](http://127.0.0.1:5000/) but since we are using GraphQL, our application is actually using [http://127.0.0.1:5000/graphql](http://127.0.0.1:5000/graphql). We use GraphiQL for our playground. Here we can test our GraphQL APIs and explore the documentation. For example, if we wanted to get all the activities, we can run the following in our GraphiQL playground

```json
# this is our query
{
    allActivities {
    edges {
      node {
        id
        name
      }
    }
  }
}
# this is our response 
{
  "data": {
    "allActivities": {
      "edges": [
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWI=",
            "name": "Contribution"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWM=",
            "name": "Transfer In"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWQ=",
            "name": "Transfer Out"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWU=",
            "name": "Buy"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkOWY=",
            "name": "Sell"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTA=",
            "name": "Dividends"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTE=",
            "name": "Withholding Tax"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTI=",
            "name": "Adjustment"
          }
        },
        {
          "node": {
            "id": "QWN0aXZpdGllczo2M2U5NWE3N2E5MTVjYTJhNGMyZGRkYTM=",
            "name": "Stock Split"
          }
        }
      ]
    }
  }
}
``
